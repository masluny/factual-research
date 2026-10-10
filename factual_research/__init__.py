"""factual-research: domain-aware research with verified citations."""
__version__ = "0.4.0"


def env(name: str, default: str = "") -> str:
    """Value of the FACTUAL_RESEARCH_<name> environment variable."""
    import os
    return os.environ.get(f"FACTUAL_RESEARCH_{name}") or default
