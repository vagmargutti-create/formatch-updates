from __future__ import annotations

import hashlib
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    sha256: str
    notes: str = ""
    encoding: str = "zip"


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def configured_manifest_url(root: Path | None = None) -> str:
    config = (root or application_root()) / "update_config.json"
    if not config.exists():
        return ""
    try:
        return str(json.loads(config.read_text(encoding="utf-8")).get("manifest_url", "")).strip()
    except (OSError, json.JSONDecodeError):
        return ""


def version_tuple(value: str) -> tuple[int, ...]:
    parts = value.strip().lstrip("v").split(".")
    return tuple(int(part) for part in parts)


def fetch_update(manifest_url: str, current_version: str) -> UpdateInfo | None:
    if not manifest_url.lower().startswith("https://"):
        raise ValueError("O endereço de atualização precisa usar HTTPS.")
    request = urllib.request.Request(manifest_url, headers={"User-Agent": "FORMATCH-Updater"})
    with urllib.request.urlopen(request, timeout=8) as response:
        data = json.loads(response.read(256_000).decode("utf-8"))
    info = UpdateInfo(
        version=str(data["version"]),
        download_url=str(data["download_url"]),
        sha256=str(data["sha256"]).lower(),
        notes=str(data.get("notes", "")),
        encoding=str(data.get("encoding", "zip")).lower(),
    )
    if not info.download_url.lower().startswith("https://"):
        raise ValueError("O pacote de atualização precisa usar HTTPS.")
    if len(info.sha256) != 64 or any(char not in "0123456789abcdef" for char in info.sha256):
        raise ValueError("A atualização publicada possui uma verificação inválida.")
    if info.encoding not in {"zip", "base64"}:
        raise ValueError("O formato do pacote de atualização não é compatível.")
    return info if version_tuple(info.version) > version_tuple(current_version) else None


def download_update(
    info: UpdateInfo, progress: Callable[[int, int], None] | None = None
) -> Path:
    request = urllib.request.Request(info.download_url, headers={"User-Agent": "FORMATCH-Updater"})
    target = Path(tempfile.gettempdir()) / f"FORMATCH-{info.version}.zip"
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as output:
        total = int(response.headers.get("Content-Length", "0"))
        done = 0
        encoded_remainder = b""
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            done += len(block)
            if info.encoding == "base64":
                encoded_remainder += b"".join(block.split())
                usable = len(encoded_remainder) - (len(encoded_remainder) % 4)
                if usable:
                    decoded = base64.b64decode(encoded_remainder[:usable], validate=True)
                    encoded_remainder = encoded_remainder[usable:]
                    output.write(decoded)
                    digest.update(decoded)
            else:
                output.write(block)
                digest.update(block)
            if progress:
                progress(done, total)
        if encoded_remainder:
            decoded = base64.b64decode(encoded_remainder, validate=True)
            output.write(decoded)
            digest.update(decoded)
    if digest.hexdigest().lower() != info.sha256:
        target.unlink(missing_ok=True)
        raise ValueError("O arquivo baixado não passou na verificação de segurança.")
    return target


def _ps(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def launch_update(package: Path, version: str, root: Path | None = None) -> None:
    if os.name != "nt":
        raise OSError("A instalação automática está disponível somente no Windows.")
    app_root = (root or application_root()).resolve()
    if not (app_root / "pyproject.toml").exists() or not (app_root / "iniciar.bat").exists():
        raise OSError("A pasta de instalação do FORMATCH não foi encontrada.")
    script = Path(tempfile.gettempdir()) / "formatch-aplicar-atualizacao.ps1"
    backup = app_root / ".formatch_rollback"
    staging = Path(tempfile.gettempdir()) / "formatch-update-extraido"
    python = app_root / ".venv" / "Scripts" / "python.exe"
    start = app_root / "iniciar.bat"
    installer = app_root / "instalar.bat"
    log = app_root / "formatch-atualizacao.log"
    content = f"""
$ErrorActionPreference = 'Stop'
$root = {_ps(app_root)}
$package = {_ps(package)}
$backup = {_ps(backup)}
$staging = {_ps(staging)}
$python = {_ps(python)}
$start = {_ps(start)}
$installer = {_ps(installer)}
$log = {_ps(log)}
function Write-UpdateLog([string]$message) {{
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Add-Content -Path $log -Value "[$stamp] $message" -Encoding UTF8
}}
function Start-Formatch {{
  Start-Process -FilePath $start -WorkingDirectory $root
}}
Set-Content -Path $log -Value '' -Encoding UTF8
Write-UpdateLog 'Processo auxiliar iniciado para a versão {version}.'
# Espera compatível também com o Windows PowerShell 5.1.
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {{
  Start-Sleep -Milliseconds 250
}}
if (Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue) {{
  Write-UpdateLog 'O aplicativo não encerrou no prazo; encerrando com segurança.'
  Stop-Process -Id {os.getpid()} -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}}
try {{
  Write-UpdateLog 'Iniciando atualização para a versão {version}.'
  if (-not (Test-Path $python)) {{
    throw 'O ambiente Python anterior não foi encontrado antes da atualização.'
  }}
  Remove-Item $backup -Recurse -Force -ErrorAction SilentlyContinue
  New-Item $backup -ItemType Directory -Force | Out-Null
  Get-ChildItem $root -Force | Where-Object {{ $_.Name -notin @('.venv', '.formatch_rollback') }} | Copy-Item -Destination $backup -Recurse -Force
  Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
  Expand-Archive -Path $package -DestinationPath $staging -Force
  Copy-Item (Join-Path $staging '*') -Destination $root -Recurse -Force
  if (-not (Test-Path $python)) {{
    throw 'O ambiente Python deixou de existir durante a atualização.'
  }}
  & $python -m pip install -e $root
  if ($LASTEXITCODE -ne 0) {{ throw 'Falha ao registrar a nova versão.' }}
  Write-UpdateLog 'Atualização concluída. Abrindo o FORMATCH.'
  Start-Formatch
}} catch {{
  Write-UpdateLog ('Falha: ' + $_.Exception.Message)
  if (Test-Path $backup) {{
    Copy-Item (Join-Path $backup '*') -Destination $root -Recurse -Force
    Write-UpdateLog 'Arquivos anteriores restaurados.'
  }}
  if (Test-Path $python) {{
    Start-Formatch
  }} elseif (Test-Path $installer) {{
    Write-UpdateLog 'Ambiente ausente. Abrindo a recuperação automática.'
    Start-Process -FilePath $installer -WorkingDirectory $root
  }}
}}
"""
    script.write_text(content.strip(), encoding="utf-8-sig")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        creationflags=flags,
        close_fds=True,
    )
