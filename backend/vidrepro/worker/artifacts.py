"""Artifact bus: every stage persists its output JSON to object storage and the
next stage loads it. Stages never pass data in memory — this is what makes any
stage individually re-runnable and debuggable against production inputs."""
from typing import Any

from vidrepro import storage


def artifact_key(org_id: str, job_id: str, stage: str) -> str:
    return f"{org_id}/artifacts/{job_id}/{stage}.json"


def save(org_id: str, job_id: str, stage: str, data: Any) -> str:
    key = artifact_key(org_id, job_id, stage)
    storage.put_json(key, data)
    return key


def load(org_id: str, job_id: str, stage: str) -> Any:
    return storage.get_json(artifact_key(org_id, job_id, stage))


def exists(org_id: str, job_id: str, stage: str) -> bool:
    return storage.object_exists(artifact_key(org_id, job_id, stage))
