from vidrepro.contracts.report import ReportBody
from vidrepro.exports.markdown import render_markdown


def github_issue_payload(body: ReportBody) -> dict:
    """Payload for POST /repos/{owner}/{repo}/issues."""
    labels = ["bug", f"vidrepro:{body.bug_type}"]
    if body.overall_confidence < 0.6:
        labels.append("needs-verification")
    return {
        "title": body.title,
        "body": render_markdown(body),
        "labels": labels,
    }
