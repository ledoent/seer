"""
Models for Seer Explorer endpoints.

These models support the full Explorer chat functionality with LLM-powered responses.
"""

from __future__ import annotations

import datetime
import enum
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class ExplorerStatus(str, enum.Enum):
    """Status of an Explorer run."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    AWAITING_USER_INPUT = "awaiting_user_input"

    @classmethod
    def terminal(cls) -> frozenset["ExplorerStatus"]:
        return frozenset((cls.COMPLETED, cls.ERROR))


class ToolCall(BaseModel):
    """A tool call made by the assistant."""

    id: str | None = None
    function: str
    args: str


class Message(BaseModel):
    """A message in the conversation."""

    role: Literal["user", "assistant", "tool_use", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class Artifact(BaseModel):
    """An artifact extracted from the conversation."""

    key: str
    data: dict[str, Any] | None = None
    reason: str = ""


class FilePatch(BaseModel):
    """A file patch representing changes."""

    path: str
    diff: str | None = None
    added: int = 0
    removed: int = 0


class MemoryBlock(BaseModel):
    """A block of memory in the Explorer conversation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: Message
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now().isoformat())
    loading: bool = False
    artifacts: list[Artifact] = Field(default_factory=list)
    file_patches: list[FilePatch] | None = None


class PendingUserInput(BaseModel):
    """Details about pending user input."""

    prompt: str
    options: list[str] | None = None


class CodingAgentState(BaseModel):
    """State of a coding agent."""

    status: str = "idle"
    message: str | None = None


class SeerRunState(BaseModel):
    """The full state of a Seer Explorer run."""

    run_id: int
    blocks: list[MemoryBlock] = Field(default_factory=list)
    status: ExplorerStatus = ExplorerStatus.PROCESSING
    updated_at: str = Field(default_factory=lambda: datetime.datetime.now().isoformat())
    pending_user_input: PendingUserInput | None = None
    repo_pr_states: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] | None = None
    coding_agents: dict[str, CodingAgentState] = Field(default_factory=dict)


# =============================================================================
# Request/Response Models
# =============================================================================


class ToolDefinition(BaseModel):
    """A custom tool definition passed from Sentry."""

    name: str
    module_path: str | None = None
    description: str = ""
    param_schema: dict[str, Any] | None = None


class ConduitParams(BaseModel):
    """Parameters for Conduit streaming support."""

    channel_id: str | None = None
    url: str | None = None


class ExplorerRunsRequest(BaseModel):
    """Request to list explorer runs."""

    organization_id: int | None = None
    category_key: str | None = None
    category_value: str | None = None


class ExplorerRunInfo(BaseModel):
    """Summary info about an explorer run."""

    run_id: int
    title: str = "Autofix Run"  # Required by Sentry
    created_at: str
    last_triggered_at: str  # Required by Sentry (replaces updated_at)
    status: ExplorerStatus
    category_key: str | None = None
    category_value: str | None = None


class ExplorerRunsResponse(BaseModel):
    """Response for listing explorer runs."""

    data: list[ExplorerRunInfo] = Field(default_factory=list)  # Sentry expects "data" key


class ExplorerChatRequest(BaseModel):
    """Request for explorer chat."""

    run_id: int | None = None
    query: str | None = None
    message: str | None = None  # Alias for query
    organization_id: int | None = None
    category_key: str | None = None
    category_value: str | None = None
    artifact_key: str | None = None
    artifact_schema: dict[str, Any] | None = None
    tools: list[ToolDefinition] | None = None
    conduit: ConduitParams | None = None
    metadata: dict[str, Any] | None = None

    class Config:
        extra = "allow"

    def get_query(self) -> str | None:
        """Get the query from either query or message field."""
        return self.query or self.message


class ExplorerChatResponse(BaseModel):
    """Response for explorer chat."""

    status: Literal["processing", "completed", "error", "not_available"] = "processing"
    run_id: int | None = None
    message: str | None = None


class ExplorerStateRequest(BaseModel):
    """Request for explorer run state."""

    run_id: int

    class Config:
        extra = "allow"


class ExplorerStateResponse(BaseModel):
    """Response for explorer run state."""

    session: SeerRunState | None = None
    status: Literal["ok", "not_found", "error"] = "ok"
    message: str | None = None


class ExplorerUpdateRequest(BaseModel):
    """Request to update explorer run."""

    run_id: int
    update_type: str | None = None
    payload: dict[str, Any] | None = None

    class Config:
        extra = "allow"


class ExplorerUpdateResponse(BaseModel):
    """Response for explorer update."""

    status: Literal["ok", "error", "not_available"] = "ok"
    message: str | None = None


# =============================================================================
# Additional stub models for missing endpoints (kept for compatibility)
# =============================================================================


class CodingAgentStateSetRequest(BaseModel):
    """Request to set coding agent state.

    Sentry sends: {run_id, coding_agent_states: [CodingAgentState]}
    """

    run_id: int
    coding_agent_states: list[dict[str, Any]] = Field(default_factory=list)

    class Config:
        extra = "allow"


class CodingAgentStateSetResponse(BaseModel):
    """Response for setting coding agent state."""

    status: Literal["ok", "error"] = "ok"
    message: str = ""


class CodingAgentStateUpdateInner(BaseModel):
    """Inner update payload for coding agent state update."""

    status: str | None = None
    agent_url: str | None = None
    results: list[dict[str, Any]] | None = None


class CodingAgentStateUpdateRequest(BaseModel):
    """Request to update coding agent state.

    Sentry sends: {agent_id, updates: {status, agent_url, results}}
    """

    agent_id: str
    updates: CodingAgentStateUpdateInner

    class Config:
        extra = "allow"


class CodingAgentStateUpdateResponse(BaseModel):
    """Response for updating coding agent state."""

    status: Literal["ok", "error"] = "ok"
    message: str = ""


class AutofixPromptRequest(BaseModel):
    """Request for autofix prompt.

    Sentry sends: {run_id, include_root_cause, include_solution}
    """

    run_id: int
    include_root_cause: bool = False
    include_solution: bool = False

    class Config:
        extra = "allow"


class AutofixPromptResponse(BaseModel):
    """Response for autofix prompt."""

    prompt: str | None = None
    message: str = ""


class CodegenPrReviewRerunRequest(BaseModel):
    """Request to rerun PR review."""

    run_id: int | None = None

    class Config:
        extra = "allow"


class CodegenPrReviewRerunResponse(BaseModel):
    """Response for PR review rerun."""

    status: Literal["ok", "not_available"] = "not_available"
    message: str = "PR review rerun not available in self-hosted mode"


class ProjectPreferenceBulkRequest(BaseModel):
    """Request for bulk project preferences."""

    project_ids: list[int] = Field(default_factory=list)

    class Config:
        extra = "allow"


class ProjectPreferenceBulkResponse(BaseModel):
    """Response for bulk project preferences."""

    preferences: dict[str, Any] = Field(default_factory=dict)
    message: str = "Bulk preferences retrieved"


class ProjectPreferenceBulkSetRequest(BaseModel):
    """Request to bulk set project preferences."""

    preferences: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class ProjectPreferenceBulkSetResponse(BaseModel):
    """Response for bulk setting project preferences."""

    status: Literal["ok", "not_available"] = "ok"
    message: str = "Bulk preferences set"


# =============================================================================
# Stub models for Sentry 26.5.0 endpoints — code review webhooks
# Sentry 26.5.0's HTTP integration callbacks for the new code-review flow.
# Self-hosted doesn't run real code-review; stubs prevent the 404s that
# otherwise show up as "Code review not available" errors in the UI.
# =============================================================================


class CodeReviewCheckRerunRequest(BaseModel):
    """Request to rerun a code-review check."""

    class Config:
        extra = "allow"


class CodeReviewCheckRerunResponse(BaseModel):
    status: Literal["ok", "not_available"] = "not_available"
    message: str = "Code review check rerun not available in self-hosted mode"


class CodeReviewPrClosedRequest(BaseModel):
    """Webhook for PR-closed."""

    class Config:
        extra = "allow"


class CodeReviewPrClosedResponse(BaseModel):
    status: Literal["ok"] = "ok"


class CodeReviewRequestPayload(BaseModel):
    """Webhook for review-request."""

    class Config:
        extra = "allow"


class CodeReviewRequestResponse(BaseModel):
    status: Literal["ok", "not_available"] = "not_available"
    message: str = "Code review request not available in self-hosted mode"


# =============================================================================
# Stub models for Sentry 26.5.0 endpoints — Launchpad / explorer indexing
# Launchpad is the new task-worker service in 26.5.0 that triggers knowledge
# indexing on seer. Without these stubs the indexing requests 404 and the
# Launchpad UI shows "Failed to index knowledge".
# =============================================================================


class ExplorerIndexRequest(BaseModel):
    """Generic explorer-index webhook payload."""

    class Config:
        extra = "allow"


class ExplorerIndexResponse(BaseModel):
    status: Literal["ok", "not_available"] = "ok"
    message: str = "Explorer indexing not implemented in self-hosted mode"


class ExplorerExportIndexesRequest(BaseModel):
    """Webhook for exporting explorer indexes."""

    class Config:
        extra = "allow"


class ExplorerExportIndexesResponse(BaseModel):
    indexes: list[dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# Stub model for Sentry 26.5.0 — capability discovery (/v1/models)
# Sentry asks seer "what models do you support" at startup; without the
# endpoint the UI shows "models unavailable" for the model picker. Empty
# list is a valid response per the API contract.
# =============================================================================


class ModelsResponse(BaseModel):
    models: list[dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# Stub models for Sentry 26.2.0 endpoints
# TODO: Implement proper handlers for these endpoints
# =============================================================================


class LlmGenerateRequest(BaseModel):
    """Request for LLM generation (issue view title generation, etc.)."""

    provider: str = "gemini"
    model: str = "flash"
    referrer: str = ""
    prompt: str = ""
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 256

    class Config:
        extra = "allow"


class LlmGenerateResponse(BaseModel):
    """Response for LLM generation."""

    content: str | None = None


class AssistedQueryStartRequest(BaseModel):
    """Request to start an assisted query search agent."""

    org_id: int | None = None
    org_slug: str = ""
    project_ids: list[int] = Field(default_factory=list)
    natural_language_query: str = ""
    strategy: str = "Traces"
    user_email: str | None = None
    timezone: str | None = None
    options: dict[str, Any] | None = None

    class Config:
        extra = "allow"


class AssistedQueryStartResponse(BaseModel):
    """Response for starting an assisted query."""

    run_id: int | None = None


class AssistedQueryStateRequest(BaseModel):
    """Request for assisted query state."""

    run_id: int
    organization_id: int | None = None

    class Config:
        extra = "allow"


class AssistedQueryStateResponse(BaseModel):
    """Response for assisted query state."""

    session: dict[str, Any] | None = None


class AssistedQueryTranslateAgenticRequest(BaseModel):
    """Request for agentic query translation."""

    org_id: int | None = None
    org_slug: str = ""
    project_ids: list[int] = Field(default_factory=list)
    natural_language_query: str = ""
    strategy: str = "Traces"
    options: dict[str, Any] | None = None

    class Config:
        extra = "allow"


class AssistedQueryTranslateAgenticResponse(BaseModel):
    """Response for agentic query translation."""

    query: str | None = None


class SupergroupsRequest(BaseModel):
    """Request for supergroups embedding."""

    organization_id: int | None = None
    group_id: int | None = None
    artifact_data: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class SupergroupsResponse(BaseModel):
    """Response for supergroups embedding."""

    status: Literal["ok", "not_available"] = "ok"


class AnomalyDetectionAlertDataRequest(BaseModel):
    """Request for anomaly detection alert data."""

    alert: dict[str, Any] = Field(default_factory=dict)
    start: float = 0
    end: float = 0

    class Config:
        extra = "allow"


class AnomalyDetectionAlertDataResponse(BaseModel):
    """Response for anomaly detection alert data."""

    success: bool = True
    message: str | None = None
    data: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowsCompareCohortRequest(BaseModel):
    """Request for workflow cohort comparison."""

    baseline: list[Any] = Field(default_factory=list)
    outliers: list[Any] = Field(default_factory=list)
    total_baseline: int = 0
    total_outliers: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class WorkflowsCompareCohortResponse(BaseModel):
    """Response for workflow cohort comparison."""

    results: list[Any] = Field(default_factory=list)
