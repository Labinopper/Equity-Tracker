from __future__ import annotations

from datetime import date


def test_weekly_review_persists_step_notes_and_archives_previous_review(client):
    payload = client.get("/api/strategic/weekly-review").json()
    as_of_date = payload["as_of_date"]

    assert payload["active_review"]["status"] == "ACTIVE"
    assert payload["summary"]["total_steps"] == 4
    assert len(payload["step_rows"]) == 4

    update_resp = client.post(
        "/weekly-review/steps/portfolio",
        data={
            "notes": "Checked deployable capital and locked buckets.",
            "completed": "true",
            "as_of": as_of_date,
        },
        follow_redirects=False,
    )
    assert update_resp.status_code == 303
    assert (
        update_resp.headers["location"]
        == f"/weekly-review?as_of={as_of_date}&msg=Weekly+review+step+saved."
    )

    after_update = client.get("/api/strategic/weekly-review").json()
    portfolio_row = next(
        row for row in after_update["step_rows"] if row["step_key"] == "portfolio"
    )
    assert portfolio_row["status"] == "COMPLETED"
    assert "deployable capital" in portfolio_row["notes"]
    assert after_update["summary"]["completed_steps"] == 1

    restart_resp = client.post(
        "/weekly-review/start",
        data={"as_of": as_of_date},
        follow_redirects=False,
    )
    assert restart_resp.status_code == 303
    assert (
        restart_resp.headers["location"]
        == f"/weekly-review?as_of={as_of_date}&msg=Weekly+review+restarted."
    )

    restarted = client.get("/api/strategic/weekly-review").json()
    assert restarted["summary"]["completed_steps"] == 0
    assert restarted["recent_reviews"]
    assert restarted["recent_reviews"][0]["completed_steps"] == 1
    assert restarted["recent_reviews"][0]["as_of_date"] == as_of_date

    page = client.get("/weekly-review")
    assert page.status_code == 200
    text = page.text
    assert "Weekly Review" in text
    assert "Review Steps" in text
    assert "Recent Reviews" in text
    assert "Review Progress" in text


def test_weekly_review_unknown_step_returns_validation_error(client):
    resp = client.post(
        "/weekly-review/steps/not-a-step",
        data={"notes": "bad", "completed": "true", "as_of": date.today().isoformat()},
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "Weekly review step not saved" in resp.text
