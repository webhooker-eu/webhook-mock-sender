from webhook_mock_sender.providers.base import EventTemplate, Provider, SignatureVerificationError
from webhook_mock_sender.providers.github import GitHubProvider
from webhook_mock_sender.providers.shopify import ShopifyProvider
from webhook_mock_sender.providers.stripe import StripeProvider

PROVIDERS: dict[str, Provider] = {
    provider.key: provider for provider in (StripeProvider(), GitHubProvider(), ShopifyProvider())
}


class UnknownProviderError(ValueError):
    pass


def get_provider(provider_key: str) -> Provider:
    provider = PROVIDERS.get(provider_key.strip().lower())
    if provider is None:
        raise UnknownProviderError(f"Unknown provider {provider_key!r}. Available: {', '.join(PROVIDERS)}")
    return provider


__all__ = [
    "PROVIDERS",
    "EventTemplate",
    "Provider",
    "SignatureVerificationError",
    "UnknownProviderError",
    "get_provider",
]
