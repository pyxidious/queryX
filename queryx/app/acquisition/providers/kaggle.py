from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from queryx.app.acquisition.models import DatasetManifest


class KaggleProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient


class KaggleProvider:
    """Adapter around Kaggle's supported Python API; no shell or CLI execution."""

    def __init__(self, credentials_path: Path, timeout_seconds: int = 120) -> None:
        if not credentials_path.is_file():
            raise KaggleProviderError("kaggle_credentials_missing", "Kaggle credentials are not configured")
        self.credentials_path = credentials_path
        self.timeout_seconds = timeout_seconds

    def _api(self):
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise KaggleProviderError("kaggle_client_unavailable", "Kaggle provider is unavailable") from exc
        api = KaggleApi()
        api.read_config_file(str(self.credentials_path))
        api.authenticate()
        configuration = getattr(getattr(api, "api_client", None), "configuration", None)
        if configuration is not None:
            configuration.timeout = self.timeout_seconds
        return api

    def inspect_dataset(self, dataset_reference: str, requested_version: str) -> DatasetManifest:
        try:
            api = self._api()
            view = api.dataset_view(dataset_reference)
            files = api.dataset_files_list(dataset_reference).files
            resolved = str(getattr(view, "version_number", None) or getattr(view, "version", None) or requested_version)
            return DatasetManifest(
                dataset_reference=dataset_reference,
                resolved_version=resolved,
                title=getattr(view, "title", None),
                license_name=getattr(view, "license_name", None),
                files=[
                    {"reference": item.name, "name": item.name, "size_bytes": getattr(item, "total_bytes", None)}
                    for item in files
                ],
            )
        except KaggleProviderError:
            raise
        except TimeoutError as exc:
            raise KaggleProviderError("kaggle_timeout", "Kaggle request timed out", transient=True) from exc
        except Exception as exc:
            raise KaggleProviderError("kaggle_access_denied", "Kaggle dataset could not be inspected") from exc

    def download_file(
        self,
        dataset_reference: str,
        resolved_version: str,
        file_reference: str,
        target: Path,
        max_bytes: int,
        heartbeat: Callable[[], None] | None = None,
    ) -> int:
        work = target.parent / f"provider-{target.stem}"
        work.mkdir(mode=0o700)
        try:
            api = self._api()
            downloaded = Path(api.dataset_download_file(dataset_reference, file_reference, path=str(work), force=True, quiet=True))
            candidates = [downloaded] if downloaded.is_file() else list(work.rglob("*"))
            source = next((item for item in candidates if item.is_file() and item.name == Path(file_reference).name), None)
            if source is None:
                raise KaggleProviderError("kaggle_download_missing", "Kaggle did not return the requested file")
            return _copy_bounded(source, target, max_bytes, heartbeat)
        except KaggleProviderError:
            raise
        except TimeoutError as exc:
            raise KaggleProviderError("kaggle_timeout", "Kaggle download timed out", transient=True) from exc
        except OSError as exc:
            raise KaggleProviderError("kaggle_download_io", "Kaggle download was interrupted", transient=True) from exc
        except Exception as exc:
            raise KaggleProviderError("kaggle_download_failed", "Kaggle file could not be downloaded", transient=True) from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)


class FakeKaggleProvider:
    def __init__(self, manifests: dict[tuple[str, str], DatasetManifest], files: dict[str, bytes]) -> None:
        self.manifests = manifests
        self.files = files
        self.failures: dict[str, KaggleProviderError] = {}

    def inspect_dataset(self, dataset_reference: str, requested_version: str) -> DatasetManifest:
        key = (dataset_reference, requested_version)
        if key not in self.manifests:
            raise KaggleProviderError("kaggle_dataset_not_found", "Kaggle dataset was not found")
        return self.manifests[key]

    def download_file(
        self,
        dataset_reference: str,
        resolved_version: str,
        file_reference: str,
        target: Path,
        max_bytes: int,
        heartbeat: Callable[[], None] | None = None,
    ) -> int:
        if file_reference in self.failures:
            raise self.failures[file_reference]
        if file_reference not in self.files:
            raise KaggleProviderError("kaggle_file_missing", "Kaggle file was not found")
        data = self.files[file_reference]
        if len(data) > max_bytes:
            raise KaggleProviderError("kaggle_file_too_large", "Kaggle file exceeds the configured limit")
        with target.open("xb") as stream:
            for offset in range(0, len(data), 64 * 1024):
                stream.write(data[offset : offset + 64 * 1024])
                if heartbeat:
                    heartbeat()
        return len(data)


def _copy_bounded(source: Path, target: Path, max_bytes: int, heartbeat: Callable[[], None] | None) -> int:
    received = 0
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        while chunk := incoming.read(1024 * 1024):
            received += len(chunk)
            if received > max_bytes:
                raise KaggleProviderError("kaggle_file_too_large", "Kaggle file exceeds the configured limit")
            outgoing.write(chunk)
            if heartbeat:
                heartbeat()
    return received
