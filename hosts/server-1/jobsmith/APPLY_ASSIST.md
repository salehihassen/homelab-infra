# Applying through the hosted Jobsmith instance

Checked on October 9, 2026 against upstream `14c0745`, the local fork at
`5f2a4b5`, and the running Docker image at `bdac6ac`.

**Security follow-up:** the [Chromium extension audit](EXTENSION_SECURITY_AUDIT.md)
found two credential-handling issues in the running build. Source fixes are now
merged into the combined fork, but the image has not been updated. Wait for the
patched deployment before installing and pairing; the workflow checks below
establish functionality, not security.

Upstream's recommended submission workflow is **Apply Assist**, using an
extension inside your normal browser. The server generates documents and maps
form fields; the extension fills the employer's application page using your
browser's existing login session. You review the result and submit yourself.

Sources: [upstream README](https://github.com/TheDevRo/Jobsmith#readme),
[getting started](https://github.com/TheDevRo/Jobsmith/blob/main/docs/getting-started-desktop.md),
and [extension instructions](https://github.com/TheDevRo/Jobsmith/blob/main/extension/README.md).
Some extension instructions describe older versions; the current code and
getting-started guide use automatic pairing and a docked panel.

## Install once on the computer where you apply

Connect that computer to Tailscale and open
`https://JOBSMITH_DOMAIN` (from `jobsmith/routing.env`). Use the same browser for Jobsmith and the job
application so the authenticated launch page opens in that browser.

Download the extension **from this instance**, rather than upstream's release:
the fork's packaged extension includes permissions and launch-page matching for
`JOBSMITH_DOMAIN`.

- **Chrome, Brave, or Edge:** download the
  [Chrome ZIP](https://JOBSMITH_DOMAIN/api/extension/download/chrome),
  extract it into a folder you will keep, open the browser's extensions page
  (`chrome://extensions`, `brave://extensions`, or `edge://extensions`), enable
  Developer mode, and Load unpacked from the extracted folder containing
  `manifest.json`. This installation persists across browser restarts.
- **Firefox:** download the
  [Firefox ZIP](https://JOBSMITH_DOMAIN/api/extension/download/firefox),
  extract it, open `about:debugging#/runtime/this-firefox`, and Load Temporary
  Add-on from its `manifest.json`. Repeat after restarting Firefox. The bundled
  upstream signed XPI lacks the hosted hostname's permission; the fork
  deliberately refuses to serve it for this deployment. A permanent Firefox
  installation needs a newly signed build of the patched extension.

Settings → Apply → Get for Chrome/Firefox also falls back to downloading the
ZIP when the server cannot save files on the browser's computer. Its surrounding
instructions still describe a local desktop installation.

After the patched image is deployed, open the extension popup, set **Backend
URL** to `https://JOBSMITH_DOMAIN`, and click **Save** to grant that host's
permission. The generic remote-support patch replaces the old hard-coded host
permission with this explicit configuration. Leave the token empty; the first
Apply Assist launch pairs through the authenticated dashboard tab.

## First application

1. Check Settings → Profile, especially work history, skills, authorization,
   sponsorship, and contact details. The stored profile supplies both tailoring
   and form answers.
2. Shortlist a job in the Inbox, open it, and click **Tailor Resume**. The
   individual button works without meeting the batch tailoring score threshold.
   Batch Tailor processes shortlisted jobs scoring at least 50. Use `honest` or
   `tailored` to keep generated claims grounded in your profile.
3. Open **Pipeline → Ready to Review** and read the generated resume and cover
   letter. Use **AI Edit** if needed. Your fork retains preparation stages before
   Applied → Interviewing → Offer.
4. Click **Apply Assist**. A new tab opens on Jobsmith's HTTPS launch page. The
   extension reads its temporary setup token, exchanges it for the persistent
   extension token, saves the HTTPS backend address, and the page redirects to
   the job site. No token copy or localhost tunnel is normally required.
5. On a new employer/ATS domain, click the extension's toolbar icon and
   **Grant access to this site** if requested. Proceed to the actual application
   form, using your normal browser login. The docked panel loads the job and its
   documents. Click **Scan**, then **Autofill**; automatic scanning is on by
   default, but **Auto-fill is off** until you enable it in the panel. Review
   highlighted fields and suggested answers. Check document attachments; use
   the resume/cover-letter tiles or a downloaded file if the site's upload
   control needs manual help. Rescan after moving to another form page.
6. Check the complete application, click the employer's **Submit** yourself,
   then **Mark Applied** in the panel or Jobsmith. Submission and recording the
   application are separate steps.

## Hosted-specific troubleshooting

- **Launch page waits for the extension:** confirm you installed this instance's
  ZIP in this browser. Allow the extension on the Jobsmith host, reload the
  launch page, and allow the new tab if the browser blocked it.
- **Popup points at localhost:** launch Apply Assist from Jobsmith first; pairing
  should replace the default address with `https://JOBSMITH_DOMAIN`.
- **Settings says the extension token cannot load:** `/api/extension/token`
  intentionally returns 403 to remote callers, even with dashboard login.
  Automatic pairing uses the launch page instead. This message does not by
  itself mean pairing is broken.
- **Signed Firefox installation returns 409:** expected for this hosted
  deployment. Use the patched Firefox ZIP as above.
- **Panel cannot scan/fill:** grant access to the ATS domain, navigate past its
  login screen, and click Scan again. The panel can fall back to scanning frames
  when the top page has no usable fields; Deep scan is also available.
- **Batch Tailor returns zero:** shortlist and score first, or use the individual
  Tailor Resume button.

`BROWSER_HEADLESS=true` and `USE_BROWSER_USE=false` affect the server's browser
automation. They do not disable the extension in your browser. The isolated
Playwright fallback runs on the server and is not the useful path for this remote setup.
Sync LinkedIn/Indeed cookies is optional for server scraping/automation; normal
Apply Assist uses the sessions already in your browser.

## Verification and limits

The live service served both extension ZIPs with the hosted hostname permission
and launch-page match (extension version 0.2.9). Authenticated profile access and
mapping email/phone fields through `/api/ext/scan` passed, with both fields mapped
from the stored profile. The AI gateway model-list check passed and advertised
all three configured model IDs; this is not a document-generation test.

The local fork's extension/frontend tests and selected backend tests cover
remote launch authentication and HTTPS URLs, extension field mapping, tailoring
guards, and hiring-pipeline behavior. The local checkout is newer than the
running image; the core extension/handoff implementation is unchanged between
those two revisions. The newer checkout includes upstream's additional manual
Applied safeguards and the latest rejection-history change.

At inspection the live database had no shortlisted jobs and only 12 manually
applied application records. Configuration did not record an extension pairing.
An actual browser-extension trial, generation of a tailored document, and
autofill on a real employer form remain to be exercised. No employer submission,
production job-status change, deployment, or extension-token rotation was made
during this inspection.
