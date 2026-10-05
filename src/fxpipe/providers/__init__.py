"""Registro de conectores. Para un país nuevo con API nueva:
1) crea providers/<kind>.py heredando de Provider,
2) agrégalo a REGISTRY,
3) declara el proveedor y sus series en config/sources.json.
"""
from .banrep_sdmx import BanrepSdmxProvider
from .banxico import BanxicoProvider
from .bcb_ptax import BcbPtaxProvider
from .bcra_cambiarias import BcraCambiariasProvider
from .base import ConfigError, ContractError, FetchRequest, Provider, ProviderError
from .socrata_trm import SocrataTrmProvider

REGISTRY: dict[str, type[Provider]] = {
    BanxicoProvider.kind: BanxicoProvider,
    SocrataTrmProvider.kind: SocrataTrmProvider,
    BcraCambiariasProvider.kind: BcraCambiariasProvider,
    BanrepSdmxProvider.kind: BanrepSdmxProvider,
    BcbPtaxProvider.kind: BcbPtaxProvider,
}


def build_provider(name: str, cfg: dict, http_get=None) -> Provider:
    kind = cfg.get("kind")
    if kind not in REGISTRY:
        raise ConfigError(f"Proveedor '{name}': tipo '{kind}' no registrado. Opciones: {sorted(REGISTRY)}")
    return REGISTRY[kind](name, cfg, http_get=http_get)


__all__ = ["REGISTRY", "build_provider", "FetchRequest", "Provider",
           "ProviderError", "ContractError", "ConfigError"]
