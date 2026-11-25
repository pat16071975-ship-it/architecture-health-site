"""Allow only exact approved source-archive additions to protected importers."""
import pathlib
import subprocess
import sys

EDITS = {
  "paid_services_upload.py": [
    [
      "import mis_source_archive\n",
      ""
    ],
    [
      "            archive_source = mis_source_archive.specs(\n                \"paid_services\", _raw, filename, None,\n                explicit_bounds=(report[\"period_start\"], report[\"period_end\"]),\n                decision=decision, actor_id=g.user[\"id\"], saved_at=now,\n                outcome=\"duplicate\" if state == \"identical\" else \"kept_old\",\n            )\n            mis_source_archive.record(conn, *archive_source[0], **archive_source[1])\n            conn.commit()\n",
      "            conn.rollback()\n"
    ],
    [
      "        archive_source = mis_source_archive.specs(\n            \"paid_services\", _raw, filename, None,\n            explicit_bounds=(report[\"period_start\"], report[\"period_end\"]),\n            decision=decision, actor_id=g.user[\"id\"], saved_at=now,\n            outcome=\"applied\",\n        )\n        mis_source_archive.record(conn, *archive_source[0], **archive_source[1])\n",
      ""
    ]
  ],
  "ident_import.py": [
    [
      "import mis_source_archive\n",
      ""
    ],
    [
      "def _decode_upload(file_storage):\n    return _decode_raw(file_storage.read())\n\n\ndef _decode_raw(raw):\n",
      "def _decode_upload(file_storage):\n    raw = file_storage.read()\n"
    ],
    [
      "def _save_import(management, service_data, finance_blobs, source, archive_sources=()):",
      "def _save_import(management, service_data, finance_blobs, source):"
    ],
    [
      "        for args, kwargs in archive_sources:\n            mis_source_archive.record(conn, *args, **kwargs)\n",
      ""
    ],
    [
      "        revenue_raw = revenue_file.read()\n        completed_raw = completed_file.read()\n        revenue_text = _decode_raw(revenue_raw)\n        completed_text = _decode_raw(completed_raw)\n",
      "        revenue_text = _decode_upload(revenue_file)\n        completed_text = _decode_upload(completed_file)\n"
    ],
    [
      "        archive_sources = mis_source_archive.pair_specs(\n            [\n                (\"services\", revenue_raw, revenue_file.filename, {row[\"date\"] for row in items}),\n                (\"completed\", completed_raw, completed_file.filename, completed_dates),\n            ],\n            written=management.keys(), actor_id=g.user[\"id\"], origin=\"ident_legacy_pair\",\n        )\n        _save_import(management, service_data, finance_blobs, source, archive_sources)",
      "        _save_import(management, service_data, finance_blobs, source)"
    ]
  ]
}

def main(base):
    for name, pairs in EDITS.items():
        path = "az-baze-auth/" + name
        old = subprocess.check_output(["git", "show", base + ":" + path])
        current = pathlib.Path(path).read_bytes()
        if current == old:
            continue
        for added, previous in pairs:
            added, previous = added.encode("utf-8"), previous.encode("utf-8")
            assert current.count(added) == 1, "Unexpected archive edit: " + path
            current = current.replace(added, previous, 1)
        assert current == old, "Protected importer differs beyond archive: " + path
    print("MIS_ARCHIVE_PROTECTED_IMPORTERS=PASS")

if __name__ == "__main__":
    main(sys.argv[1])
