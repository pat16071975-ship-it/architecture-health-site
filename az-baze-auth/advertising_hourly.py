"""Prepared hourly refresh entrypoint; NOT scheduled or enabled by this file.

Runs the same transactional Metrika+Direct core used by the existing button.
No browser, CSRF/session bypass endpoint, extra API token, or separate data path.
The systemd service and timer must be separately approved before activation.
"""
import json
import sys

from app import app, audit, db
import advertising_report
import metrika_auto


def main():
    token = metrika_auto.token_from_env()
    if not token:
        print("AZ_HOURLY_SYNC=ERROR reason=missing_yandex_token", file=sys.stderr)
        return 2
    with app.app_context():
        conn = db()
        try:
            result = advertising_report.run_metrika_direct_sync(
                conn, token, actor_user_id=None
            )
            if result["changed"]:
                audit(
                    "advertising_metrika_direct_hourly_sync",
                    details=result["audit_details"],
                )
            payload = result["payload"]
            print(json.dumps({
                "status": "ok",
                "direct_rows": payload["direct_rows"],
                "direct_period": payload["direct_period"],
                "direct_reused": payload["direct_reused"],
                "completed_updated": len(payload["completed_days"]),
                "current_updated": payload["current_updated"],
            }, ensure_ascii=False, sort_keys=True))
            return 0
        except metrika_auto.MetrikaBusyError:
            # Concurrent manual click/another worker: shared lock ensures
            # no overlapping imports. Skipping is expected, not an error.
            print("AZ_HOURLY_SYNC=SKIPPED busy=another_sync_running")
            return 0
        except (
            metrika_auto.MetrikaApiError,
            ValueError,
            OSError,
            Exception,
        ) as exc:
            # Never log OAuth tokens, raw API bodies, or credentials.
            # DirectApiError text is constructed from numeric HTTP/error codes.
            try:
                conn.rollback()
            except Exception:
                pass
            message = (
                str(exc) if isinstance(exc, ValueError)
                else type(exc).__name__
            )
            print("AZ_HOURLY_SYNC=ERROR "+message, file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
