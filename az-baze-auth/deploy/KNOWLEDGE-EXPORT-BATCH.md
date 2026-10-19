# Pilot MP4 batches for manual transfer

Owner approved batch-by-batch local VPS preparation, download, and manual placement on QNAP on 2026-10-19. This standalone export is the first step, NOT deployment of PR #130 and not conversion of the whole library.

## Scope

- Read ONLY explicit existing files under `/var/lib/az-baze/pilot-remux`.
- Write an owner-only ZIP and receipts under `/var/lib/az-baze/video-batches/AZ-VIDEO-BATCH-NNN`.
- Preserve original directory structure and base names in `video/`; preserve `.MP4` case where already MP4, change other extensions to `.mp4`.
- No QNAP access at all, no ffmpeg, no service restart, no database writes, no public web route, no original-file archive, no catalog publication.
- Only ffprobe is invoked for current codec/size/duration inspection. Packaging does not claim that all timing, subtitle, quality, or browser compatibility questions are closed.
- Private course names and mappings are supplied by the operator as JSON on stdin and MUST NOT be committed to Git.

## Operation

Use `python3 knowledge_export_batch.py --apply --batch AZ-VIDEO-BATCH-001` as root on az-server, with a JSON array on stdin. Each item has `input` (MP4 basename), `original` (exact relative source path), `bytes`, `duration_sec`, and optional `note` (unresolved review points). Defaults cannot silently select an arbitrary source folder.

Fresh disk admission is payload plus 8 GiB reserve plus 16 MiB overhead. Check free disk throughout writing/verification. ZIP maximum is 5 GiB. A completed duplicate run verifies and reuses the existing package. Failed staging stays for inspection; a retry refuses to create another copy. No delete/cleanup subcommand is provided.

Delivery is via the owner's existing SSH/SFTP connection, not a publicly accessible URL. `ZIP=` reports a server path, not a download link. Windows download/ZIP checksum and actual manual placement still require confirmation; do not mark DOWNLOAD or CATALOG_PUBLICATION complete from an export log.

The ZIP has converted pilot files, NOT original backups. Extract into a separate temporary local directory first. Read per-file review notes. Never unpack over originals: preserve originals by the separately approved archival process. The existing site's old cache is not automatically bypassed by downloading a batch.

## Tests

Unit tests: exact round-trip ZIP membership/bytes/names; case collisions; traversal/symlinks; re-run stability; modified pilot and damaged ZIP rejection; disk/size limits; incomplete-job guard; concurrency lock; private output permissions. Real FFmpeg is used ONLY inside one isolated synthetic-media test to produce H.264/AAC, then ffprobe/package/extract equality is verified. Production data is not used by tests.
