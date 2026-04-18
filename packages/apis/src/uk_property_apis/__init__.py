"""UK property government and public API clients."""

from __future__ import annotations

from uk_property_apis.companies_house import (
    CompaniesHouseClient,
    LandlordGraph,
    LandlordGraphEdge,
    LandlordGraphNode,
    build_landlord_graph,
)
from uk_property_apis.epc import EPCClient
from uk_property_apis.flood import FloodClient
from uk_property_apis.idox import (
    ApplicationDetail,
    ArcGISPlanningClient,
    CouncilConfig,
    HTMLPlanningClient,
    PlanningApplication,
)
from uk_property_apis.land_registry import LandRegistryClient
from uk_property_apis.ons import ONSClient
from uk_property_apis.planning import PlanningClient
from uk_property_apis.police import PoliceClient
from uk_property_apis.postcodes import PostcodesClient
from uk_property_apis.tenders import (
    ContractsFinderClient,
    FTSClient,
    Tender,
    TenderClassification,
    TenderLocation,
    TenderOrg,
    TenderQuery,
    TenderSource,
    TenderStatus,
    TenderValue,
)
from uk_property_apis.voa import VOAClient

__version__ = "0.1.0"

__all__ = [
    "ApplicationDetail",
    "ArcGISPlanningClient",
    "CompaniesHouseClient",
    "ContractsFinderClient",
    "CouncilConfig",
    "EPCClient",
    "FTSClient",
    "FloodClient",
    "HTMLPlanningClient",
    "LandRegistryClient",
    "LandlordGraph",
    "LandlordGraphEdge",
    "LandlordGraphNode",
    "ONSClient",
    "PlanningApplication",
    "PlanningClient",
    "PoliceClient",
    "PostcodesClient",
    "Tender",
    "TenderClassification",
    "TenderLocation",
    "TenderOrg",
    "TenderQuery",
    "TenderSource",
    "TenderStatus",
    "TenderValue",
    "VOAClient",
    "__version__",
    "build_landlord_graph",
]
