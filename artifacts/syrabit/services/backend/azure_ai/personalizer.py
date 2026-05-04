"""Azure Personalizer wrapper — next-best-quiz A/B surface.

Wired to the next-best-quiz suggestion under the
``recs.next_quiz_provider`` feature flag. Values:

* ``deterministic`` (default) — the existing rule-based ranker.
* ``personalizer`` — Azure Personalizer rank/reward loop.
* ``shadow`` — call Personalizer for impression logging only;
  return the deterministic pick to the user. Used to warm the
  reward model before flipping users in.

Reward signal is the user's actual quiz attempt within 30 minutes of
the impression — recorded by ``record_reward`` from the quiz-attempt
hook. Per the task this is an A/B surface, scoped to next-best-quiz
only — Amazon Personalize stays the production recommender for
broader surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import _resolver

API_VERSION = "v1.0"


@dataclass
class RankAction:
    id: str
    features: list[dict] = field(default_factory=list)


@dataclass
class RankDecision:
    event_id: str
    reward_action_id: str
    ranking: list[tuple[str, float]] = field(default_factory=list)


def _token() -> str:
    return _resolver.get_credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token


def rank(
    *,
    event_id: str,
    actions: list[RankAction],
    context_features: list[dict],
    excluded_action_ids: Optional[list[str]] = None,
    defer_activation: bool = False,
) -> RankDecision:
    """Ask Personalizer to rank candidate quizzes.

    ``defer_activation=True`` is required for ``shadow`` mode — the
    impression is recorded but doesn't count for reward attribution
    until ``activate_event`` is called explicitly. This keeps the
    reward model from being polluted by impressions the user never
    actually saw.
    """
    import requests

    endpoint = _resolver.endpoint_for("personalizer").rstrip("/")
    body = {
        "eventId": event_id,
        "actions": [{"id": a.id, "features": a.features} for a in actions],
        "contextFeatures": context_features,
        "excludedActions": excluded_action_ids or [],
        "deferActivation": defer_activation,
    }
    resp = requests.post(
        f"{endpoint}/personalizer/{API_VERSION}/rank",
        json=body,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=5,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-personalizer: throttled (429)")
    resp.raise_for_status()
    payload = resp.json()
    ranking = [
        (row["id"], float(row.get("probability", 0.0)))
        for row in payload.get("ranking", [])
    ]
    return RankDecision(
        event_id=event_id,
        reward_action_id=payload.get("rewardActionId", actions[0].id if actions else ""),
        ranking=ranking,
    )


def record_reward(event_id: str, reward: float) -> None:
    """Record the reward (typically 0.0–1.0) for a prior rank call.

    The quiz-attempt hook calls this with reward = quiz_score / 100
    when the user attempts the recommended quiz within 30 min;
    otherwise it calls with reward = 0.0 from the cleanup cron.
    """
    import requests

    if not 0.0 <= reward <= 1.0:
        raise ValueError(f"reward {reward} outside [0,1]")
    endpoint = _resolver.endpoint_for("personalizer").rstrip("/")
    resp = requests.post(
        f"{endpoint}/personalizer/{API_VERSION}/events/{event_id}/reward",
        json={"value": reward},
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=5,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-personalizer: reward throttled (429)")
    resp.raise_for_status()


def activate_event(event_id: str) -> None:
    """Activate a deferred rank impression (shadow → live promotion)."""
    import requests

    endpoint = _resolver.endpoint_for("personalizer").rstrip("/")
    resp = requests.post(
        f"{endpoint}/personalizer/{API_VERSION}/events/{event_id}/activate",
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=5,
    )
    resp.raise_for_status()
