"""Backup failure alerts: one message per incident, silence on success, no root needed.

Runs the real CLI against a fake systemctl, a fake Ping Me helper and a fixed
clock. Set PING_ME_HELPER_FOR_TESTS to a real ping_me.py to also validate the
message format with that helper's --dry-run (no credentials, no network).
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
ALERTS = REPO / 'hosts/server-1/backup/alerts'
SCRIPT = ALERTS / 'infra-backup-alert'
BACKUP = 'infra-backup.service'
MAINT = 'infra-backup-maintenance.service'
T0 = 1_800_000_000
H = 3600

FAKE_SYSTEMCTL = r'''
import json, os, sys
args = sys.argv[1:]
assert args[0] == "show" and "--timestamp=unix" in args, args
unit = [a for a in args[1:] if not a.startswith("-")][0]
props = args[args.index("-p") + 1].split(",")
state = json.load(open(os.environ["FAKE_SYSTEMCTL_STATE"])).get(unit, {})
for p in props:
    print(f"{p}={state.get(p, '')}")
'''

FAKE_HELPER = r'''
import json, os, sys
with open(os.environ["FAKE_HELPER_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
sys.exit(int(os.environ.get("FAKE_HELPER_EXIT", "0")))
'''


class AlertHarness:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / 'fake_systemctl.py').write_text(FAKE_SYSTEMCTL)
        (self.root / 'fake_helper.py').write_text(FAKE_HELPER)
        self.units = {}
        self.write_units()

    def write_units(self):
        (self.root / 'units.json').write_text(json.dumps(self.units))

    def unit(self, name, **props):
        self.units[name] = props
        self.write_units()

    def env(self, now, transport='dry-run', helper=None, helper_exit=0):
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(('ALERT_', 'PING_ME_', 'SERVICE_', 'EXIT_')):
                del env[key]
        env.update({
            'ALERT_NOW': str(now),
            'ALERT_TRANSPORT': transport,
            'ALERT_STATE_DIR': str(self.root / 'state'),
            'BACKUP_STATUS_DIR': str(self.root / 'status'),
            'ALERT_DRY_RUN_LOG': str(self.root / 'sent.jsonl'),
            'ALERT_SYSTEMCTL': f'{shlex.quote(sys.executable)} {shlex.quote(str(self.root / "fake_systemctl.py"))}',
            'FAKE_SYSTEMCTL_STATE': str(self.root / 'units.json'),
            'FAKE_HELPER_LOG': str(self.root / 'helper.jsonl'),
            'FAKE_HELPER_EXIT': str(helper_exit),
        })
        if helper:
            env['PING_ME_HELPER'] = str(helper)
        return env

    def run(self, *args, now, **kwargs):
        extra = kwargs.pop('extra_env', {})
        env = self.env(now, **kwargs)
        env.update(extra)
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def sent(self):
        path = self.root / 'sent.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def helper_calls(self):
        path = self.root / 'helper.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def succeed(self, unit, at):
        result = self.run('record-result', unit, now=at,
                          extra_env={'SERVICE_RESULT': 'success', 'EXIT_STATUS': '0'})
        assert result.returncode == 0, result.stderr

    def fail(self, unit, at):
        self.unit(unit, Result='exit-code', ExecMainCode='1', ExecMainStatus='1',
                  ExecMainExitTimestamp=f'@{at}', ActiveState='failed')
        return self.run('on-failure', unit, now=at)


class BackupAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = AlertHarness(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # --- success bookkeeping -------------------------------------------------

    def test_record_result_writes_marker_only_for_success_and_never_fails(self):
        marker = Path(self.tmp.name, 'status', f'{BACKUP}.success')
        failed = self.h.run('record-result', BACKUP, now=T0,
                            extra_env={'SERVICE_RESULT': 'exit-code', 'EXIT_STATUS': '1'})
        self.assertEqual(failed.returncode, 0)
        self.assertFalse(marker.exists())
        self.h.succeed(BACKUP, T0)
        self.assertEqual(json.loads(marker.read_text())['finished_at'], T0)
        bad = self.h.run('record-result', '../../etc/passwd.service', now=T0,
                         extra_env={'SERVICE_RESULT': 'success'})
        self.assertEqual(bad.returncode, 0)  # Bookkeeping errors never fail the backup.
        unwritable = self.h.run('record-result', BACKUP, now=T0,
                                extra_env={'SERVICE_RESULT': 'success', 'BACKUP_STATUS_DIR': '/proc/nope'})
        self.assertEqual(unwritable.returncode, 0)
        self.assertEqual(self.h.sent(), [])  # Success never posts.

    # --- OnFailure path ------------------------------------------------------

    def test_repeated_failures_post_once_per_window(self):
        self.h.succeed(BACKUP, T0 - 24 * H)
        first = self.h.fail(BACKUP, T0)
        self.assertEqual(first.returncode, 0, first.stderr)
        for hours in (1, 2, 12, 23):
            self.assertEqual(self.h.fail(BACKUP, T0 + hours * H).returncode, 0)
        sent = self.h.sent()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['kind'], 'failure')
        self.assertIn('Backup job failed: infra-backup.service', sent[0]['title'])
        self.assertIn('exit status 1', sent[0]['message'])
        self.assertIn('Last success:', sent[0]['message'])
        # After the window an unresolved job may remind once, with the count.
        self.h.fail(BACKUP, T0 + 25 * H)
        sent = self.h.sent()
        self.assertEqual(len(sent), 2)
        self.assertIn('Still failing: 4 more failure(s)', sent[1]['message'])

    def test_success_clears_incident_silently_and_next_failure_alerts_at_once(self):
        self.h.fail(BACKUP, T0)
        self.h.succeed(BACKUP, T0 + 2 * H)
        self.assertEqual(len(self.h.sent()), 1)  # No recovery message.
        self.h.fail(BACKUP, T0 + 3 * H)
        sent = self.h.sent()
        self.assertEqual(len(sent), 2)
        self.assertNotIn('Still failing', sent[1]['message'])

    def test_jobs_are_deduplicated_independently(self):
        self.h.fail(BACKUP, T0)
        self.h.fail(MAINT, T0 + 60)
        self.assertEqual([s['unit'] for s in self.h.sent()], [BACKUP, MAINT])

    def test_systemd_record_of_this_boot_counts_as_success(self):
        self.h.unit(BACKUP, Result='success', ExecMainStatus='0', ActiveState='inactive',
                    ExecMainExitTimestamp=f'@{T0 - 2 * H}')
        self.assertEqual(self.h.run('check', now=T0).returncode, 0)
        self.assertEqual(self.h.run('check', now=T0 + 30 * H).returncode, 0)
        self.assertEqual(self.h.sent(), [])

    # --- missed-completion check ---------------------------------------------

    def test_overdue_backup_alerts_once_then_daily(self):
        self.h.succeed(BACKUP, T0)
        self.h.succeed(MAINT, T0)
        for hours in range(1, 37):
            self.h.run('check', now=T0 + hours * H)
        self.assertEqual(self.h.sent(), [])  # 36h is still within the limit.
        for hours in range(37, 60):
            self.h.run('check', now=T0 + hours * H)
        sent = self.h.sent()
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]['kind'], sent[0]['unit']), ('stale', BACKUP))
        self.assertIn('limit 36.0h', sent[0]['message'])
        self.assertIn('infra-backup.timer', sent[0]['message'])
        self.h.run('check', now=T0 + 62 * H)
        self.assertEqual(len(self.h.sent()), 2)

    def test_maintenance_uses_eight_day_limit(self):
        self.h.succeed(BACKUP, T0 + 7 * 24 * H)
        self.h.succeed(MAINT, T0)
        self.h.run('check', now=T0 + 7 * 24 * H + 1)
        self.assertEqual(self.h.sent(), [])
        self.h.run('check', now=T0 + 8 * 24 * H + 60)
        self.assertEqual([s['unit'] for s in self.h.sent()], [MAINT])

    def test_never_succeeded_alerts_only_after_limit_from_first_check(self):
        self.h.run('check', now=T0)  # Fresh install: records monitoring start.
        self.h.run('check', now=T0 + 30 * H)
        self.assertEqual(self.h.sent(), [])
        self.h.run('check', now=T0 + 37 * H)
        sent = self.h.sent()
        self.assertEqual([s['unit'] for s in sent], [BACKUP])
        self.assertIn('No success recorded since monitoring started', sent[0]['message'])

    def test_failure_alert_suppresses_overdue_alert_for_same_incident(self):
        self.h.succeed(BACKUP, T0 - 30 * H)
        self.h.succeed(MAINT, T0)
        self.h.fail(BACKUP, T0)
        self.h.run('check', now=T0 + 10 * H)  # Overdue (40h) but already reported.
        self.assertEqual([s['kind'] for s in self.h.sent()], ['failure'])

    def test_custom_job_list_and_repeat_interval(self):
        env = {'ALERT_JOBS': 'other.service=90m', 'ALERT_REPEAT_HOURS': '2'}
        self.h.succeed('other.service', T0)
        self.h.run('check', now=T0 + 91 * 60, extra_env=env)
        self.h.run('check', now=T0 + 2 * H, extra_env=env)
        self.h.run('check', now=T0 + 4 * H, extra_env=env)
        self.assertEqual([s['unit'] for s in self.h.sent()], ['other.service'] * 2)
        bad = self.h.run('check', now=T0, extra_env={'ALERT_JOBS': 'x.service=soon'})
        self.assertEqual(bad.returncode, 2)

    # --- transport -----------------------------------------------------------

    def test_ping_me_invocation(self):
        helper = Path(self.tmp.name, 'fake_helper.py')
        self.h.unit(BACKUP, Result='exit-code', ExecMainStatus='1', ExecMainExitTimestamp=f'@{T0}')
        result = self.h.run('on-failure', BACKUP, now=T0, transport='ping-me', helper=helper)
        self.assertEqual(result.returncode, 0, result.stderr)
        (call,) = self.h.helper_calls()
        self.assertEqual(call[:3], ['send', '--mode', 'notify'])
        self.assertNotIn('--dry-run', call)
        request_id = call[call.index('--request-id') + 1]
        self.assertRegex(request_id, r'^[A-Za-z0-9_-]{1,64}$')
        self.assertIn('Backup job failed', call[call.index('--title') + 1])
        self.assertTrue(call[call.index('--authorized-scope') + 1].strip())

    def test_failed_delivery_is_retried_by_check_and_dropped_after_success(self):
        helper = Path(self.tmp.name, 'fake_helper.py')
        self.h.unit(BACKUP, Result='exit-code', ExecMainStatus='1', ExecMainExitTimestamp=f'@{T0}')
        failed = self.h.run('on-failure', BACKUP, now=T0, transport='ping-me', helper=helper, helper_exit=2)
        self.assertEqual(failed.returncode, 1)
        retried = self.h.run('check', now=T0 + H, transport='ping-me', helper=helper)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(len(self.h.helper_calls()), 2)
        self.h.run('check', now=T0 + 2 * H, transport='ping-me', helper=helper)
        self.assertEqual(len(self.h.helper_calls()), 2)  # Delivered: no third send.
        # A pending alert resolved by a success before delivery is dropped silently.
        self.h.run('on-failure', MAINT, now=T0, transport='ping-me', helper=helper, helper_exit=2)
        self.h.succeed(MAINT, T0 + 30 * 60)
        self.h.run('check', now=T0 + 3 * H, transport='ping-me', helper=helper)
        maint_calls = [c for c in self.h.helper_calls() if MAINT in c[c.index('--title') + 1]]
        self.assertEqual(len(maint_calls), 1)

    def test_delivery_retries_are_bounded(self):
        helper = Path(self.tmp.name, 'fake_helper.py')
        self.h.succeed(MAINT, T0)
        self.h.unit(BACKUP, Result='exit-code', ExecMainStatus='1', ExecMainExitTimestamp=f'@{T0}')
        self.h.run('on-failure', BACKUP, now=T0, transport='ping-me', helper=helper, helper_exit=2)
        for hours in range(1, 6):
            self.h.run('check', now=T0 + hours * H, transport='ping-me', helper=helper, helper_exit=2)
        self.assertEqual(len(self.h.helper_calls()), 3)

    def test_overdue_delivery_budget_is_not_reset_by_hourly_checks(self):
        helper = Path(self.tmp.name, 'fake_helper.py')
        self.h.succeed(BACKUP, T0 - 40 * H)
        self.h.succeed(MAINT, T0)
        for hours in range(26):
            self.h.run('check', now=T0 + hours * H, transport='ping-me',
                       helper=helper, helper_exit=2)
        self.assertEqual(len(self.h.helper_calls()), 3)
        # A new reminder window can retry after the configured cooldown.
        for hours in (26, 27, 28, 29):
            self.h.run('check', now=T0 + hours * H, transport='ping-me',
                       helper=helper, helper_exit=2)
        self.assertEqual(len(self.h.helper_calls()), 6)

    def test_repeated_failure_hooks_share_the_pending_delivery_budget(self):
        helper = Path(self.tmp.name, 'fake_helper.py')
        for seconds in (0, 60, 120, 180):
            self.h.run('on-failure', BACKUP, now=T0 + seconds, transport='ping-me',
                       helper=helper, helper_exit=2)
        self.assertEqual(len(self.h.helper_calls()), 3)
        self.h.succeed(BACKUP, T0 + 200)
        self.h.run('on-failure', BACKUP, now=T0 + 240, transport='ping-me', helper=helper)
        self.assertEqual(len(self.h.helper_calls()), 4)

    def test_success_before_delivery_gives_the_next_failure_a_fresh_budget(self):
        helper = Path(self.tmp.name, 'fake_helper.py')
        self.h.run('on-failure', BACKUP, now=T0, transport='ping-me',
                   helper=helper, helper_exit=2)
        self.h.succeed(BACKUP, T0 + 10)
        for seconds in (20, 30, 40, 50):
            self.h.run('on-failure', BACKUP, now=T0 + seconds, transport='ping-me',
                       helper=helper, helper_exit=2)
        self.assertEqual(len(self.h.helper_calls()), 4)

    def test_missing_helper_is_reported_not_crashing(self):
        self.h.unit(BACKUP, Result='exit-code', ExecMainStatus='1')
        result = self.h.run('on-failure', BACKUP, now=T0, transport='ping-me', helper='/nonexistent/ping_me.py')
        self.assertEqual(result.returncode, 1)
        self.assertIn('PING_ME_HELPER', result.stderr)

    def test_test_message_is_labelled_and_leaves_state_alone(self):
        result = self.h.run('test-message', now=T0)
        self.assertEqual(result.returncode, 0, result.stderr)
        (sent,) = self.h.sent()
        self.assertTrue(sent['title'].startswith('[TEST] '))
        self.assertTrue(sent['title'].endswith('ignore'))
        self.assertFalse(Path(self.tmp.name, 'state').exists())

    @unittest.skipUnless(os.environ.get('PING_ME_HELPER_FOR_TESTS'), 'set PING_ME_HELPER_FOR_TESTS')
    def test_real_helper_accepts_messages_in_dry_run(self):
        helper = os.environ['PING_ME_HELPER_FOR_TESTS']
        self.h.succeed(BACKUP, T0 - 40 * H)
        for args in (('on-failure', BACKUP), ('check',), ('test-message',)):
            if args[0] == 'on-failure':
                self.h.unit(BACKUP, Result='exit-code', ExecMainStatus='1', ExecMainExitTimestamp=f'@{T0}')
            result = self.h.run(*args, now=T0 + (30 * H if args == ('check',) else 0), helper=helper)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"status": "dry_run"', result.stderr)


class UnitFileTests(unittest.TestCase):
    def test_units_reference_installed_script_and_never_chain(self):
        for unit in (ALERTS / 'units').iterdir():
            text = unit.read_text()
            self.assertNotRegex(text, r'(?m)^OnFailure=', unit.name)
            if unit.suffix == '.service':
                self.assertIn('ExecStart=/usr/local/sbin/infra-backup-alert ', text)
                self.assertIn('EnvironmentFile=/etc/infra-backup/alert.env', text)
                self.assertNotRegex(text, r'(?m)^User=', 'User comes from the install-time drop-in')

    def test_dropin_cannot_change_backup_result(self):
        text = (ALERTS / 'dropins/50-failure-alert.conf').read_text()
        self.assertIn('OnFailure=infra-backup-alert@%n.service', text)
        self.assertRegex(text, r'(?m)^ExecStopPost=-/usr/local/sbin/infra-backup-alert record-result %n$')

    def test_env_example_has_no_credentials(self):
        settings = [line for line in (ALERTS / 'alert.env.example').read_text().splitlines()
                    if line and not line.startswith('#')]
        self.assertEqual(sorted(line.split('=')[0] for line in settings),
                         ['ALERT_JOBS', 'ALERT_REPEAT_HOURS', 'ALERT_TRANSPORT', 'PING_ME_HELPER'])

    def test_install_script_parses(self):
        subprocess.run(['bash', '-n', str(ALERTS / 'install.sh')], check=True)


if __name__ == '__main__':
    unittest.main()
