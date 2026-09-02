import os
from dataclasses import dataclass, field
from pathlib import Path


def environment(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True, repr=False)
class SecretValue:
    _value: str = field(repr=False)

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('**********')"


def environment_secret(name: str, default: str) -> SecretValue:
    return SecretValue(environment(name, default))


@dataclass(frozen=True)
class Settings:
    surreal_url: str = field(
        default_factory=lambda: environment("SURREAL_URL", "http://surrealdb:8000")
    )
    surreal_root_user: str = field(default_factory=lambda: environment("SURREAL_ROOT_USER", "root"))
    surreal_root_password: SecretValue = field(
        default_factory=lambda: environment_secret("SURREAL_ROOT_PASSWORD", "root")
    )
    surreal_agent_user: str = field(
        default_factory=lambda: environment("SURREAL_AGENT_USER", "agent")
    )
    surreal_agent_password: SecretValue = field(
        default_factory=lambda: environment_secret("SURREAL_AGENT_PASSWORD", "agent-dev-password")
    )
    surreal_runtime_user: str = field(
        default_factory=lambda: environment("SURREAL_RUNTIME_USER", "runtime")
    )
    surreal_runtime_password: SecretValue = field(
        default_factory=lambda: environment_secret(
            "SURREAL_RUNTIME_PASSWORD", "runtime-dev-password"
        )
    )
    ops_namespace: str = field(default_factory=lambda: environment("OPS_NAMESPACE", "argos"))
    ops_database: str = field(default_factory=lambda: environment("OPS_DATABASE", "ops"))
    agno_namespace: str = field(default_factory=lambda: environment("AGNO_NAMESPACE", "agno"))
    agno_database: str = field(default_factory=lambda: environment("AGNO_DATABASE", "sessions"))
    schema_path: Path = field(
        default_factory=lambda: Path(environment("SCHEMA_PATH", "db/schema.surql"))
    )

    litellm_base_url: str = field(
        default_factory=lambda: environment("LITELLM_BASE_URL", "http://litellm:4000")
    )
    litellm_master_key: SecretValue = field(
        default_factory=lambda: environment_secret("LITELLM_MASTER_KEY", "sk-argos-master-key")
    )

    langfuse_host: str = field(
        default_factory=lambda: environment("LANGFUSE_HOST", "http://langfuse-web:3000")
    )
    langfuse_public_key: str = field(default_factory=lambda: environment("LANGFUSE_PUBLIC_KEY", ""))
    langfuse_secret_key: SecretValue = field(
        default_factory=lambda: environment_secret("LANGFUSE_SECRET_KEY", "")
    )

    @property
    def mcp_url(self) -> str:
        return f"{self.surreal_url.rstrip('/')}/mcp"

    @property
    def surreal_ws_url(self) -> str:
        scheme, _, rest = self.surreal_url.partition("://")
        ws_scheme = "wss" if scheme == "https" else "ws"
        return f"{ws_scheme}://{rest.rstrip('/')}"

    @property
    def root_auth(self) -> tuple[str, str]:
        return (self.surreal_root_user, self.surreal_root_password.get_secret_value())
