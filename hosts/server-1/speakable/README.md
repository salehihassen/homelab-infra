# speakable

Markdown-to-speech-text converter (`SPEAK_DOMAIN`). Stateless. The image is
published to GHCR by the source repository's CI (`latest` and `sha-<commit>`)
and set as `SPEAKABLE_IMAGE` in `.env`.

- **Requires:** ai-gateway (client key in `/opt/speakable/secrets`).
- Caddy caps request bodies at 256 KB; keep that aligned with
  `conversion.max_body_bytes` in `/opt/speakable/config.yaml`.
- **Backup:** `/opt/speakable` (config and secrets).
