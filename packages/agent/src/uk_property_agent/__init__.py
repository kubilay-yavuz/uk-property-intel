"""Natural-language UK property intelligence agent (LangGraph)."""

from __future__ import annotations

from uk_property_agent.agent import PropertyAgent, ToolContext
from uk_property_agent.dossier import (
    DossierAmenityCategory,
    DossierAVM,
    DossierCrime,
    DossierEPC,
    DossierError,
    DossierFlood,
    DossierLocation,
    DossierNeighbourhood,
    DossierOptions,
    DossierPlanning,
    DossierPPD,
    DossierSaleStat,
    PropertyDossier,
    build_property_dossier,
)
from uk_property_agent.prompts import (
    CACHEABLE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_system_message,
)
from uk_property_agent.providers import (
    Provider,
    ProviderSpec,
    TaskKind,
    build_chat_model,
    cache_shape_for,
    describe_env,
    has_credentials,
    resolve_provider,
    select_available_provider,
)
from uk_property_agent.tools import ALL_TOOLS, build_tools

__version__ = "0.3.0"

__all__ = [
    "ALL_TOOLS",
    "CACHEABLE_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "DossierAVM",
    "DossierAmenityCategory",
    "DossierCrime",
    "DossierEPC",
    "DossierError",
    "DossierFlood",
    "DossierLocation",
    "DossierNeighbourhood",
    "DossierOptions",
    "DossierPPD",
    "DossierPlanning",
    "DossierSaleStat",
    "PropertyAgent",
    "PropertyDossier",
    "Provider",
    "ProviderSpec",
    "TaskKind",
    "ToolContext",
    "__version__",
    "build_chat_model",
    "build_property_dossier",
    "build_system_message",
    "build_tools",
    "cache_shape_for",
    "describe_env",
    "has_credentials",
    "resolve_provider",
    "select_available_provider",
]
