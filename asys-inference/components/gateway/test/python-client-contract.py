from __future__ import annotations

import sys

from cyclo.gateway_client import (
    get_usage,
    list_login_providers,
    login,
    logout,
    rename,
)
from cyclo.provider_client import list_models


def main() -> None:
    port = int(sys.argv[1])
    account = f"{'a' * 63}-"
    public_model = f"{account}/{'m' * 1024}"

    providers = list_login_providers(port)
    assert providers == (
        {
            "id": "work-",
            "description": "d" * 1025,
            "oauth": False,
            "api_key": True,
        },
    )

    usage = get_usage(port)
    assert tuple(usage["by_provider"]) == (account,)
    assert tuple(usage["by_model"]) == (public_model,)
    model = list_models(port)["models"][0]
    assert model["id"] == public_model
    assert model["displayName"] == "Generated contract model"
    assert model["capabilities"]["inputModalities"] == [
        "MODALITY_TEXT",
        "MODALITY_IMAGE",
    ]
    assert model["capabilities"]["outputModalities"] == ["MODALITY_TEXT"]
    assert model["capabilities"]["functionTools"] is True
    assert model["capabilities"]["parallelToolCalls"] is True
    assert model["capabilities"]["reasoning"] is True
    assert model["contextWindowTokens"] == "1048576"
    assert model["maxOutputTokens"] == "65536"
    assert isinstance(model["inferenceFormat"], str) and model["inferenceFormat"]

    assert login(
        port,
        "work-",
        account="team_",
        authentication="api_key",
        interactive=False,
    ) == {"account": "team_", "authentication": "api_key"}
    assert logout(port, "team_") == "team_"
    assert rename(port, "team_", "personal-") == ("team_", "personal-")


if __name__ == "__main__":
    main()
