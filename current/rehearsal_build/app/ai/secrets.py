"""Local owner-only secret, outside project and ZIP. No credential is returned to HTTP clients."""
import os,stat
from pathlib import Path

def secret_path():return Path.home()/'Library/Application Support/ResearchWorkbench/meta_api_key'
def baseten_secret_path():return Path.home()/'Library/Application Support/ResearchWorkbench/baseten_api_key'
def modal_shared_secret_path():return secret_path().with_name('modal_shared_api_key')
def openai_secret_path():return secret_path().with_name('openai_api_key')
def _owner_only(path):
    """Read an owner-only secret file, or None when absent.

    Same regular-file / uid / mode-600 gate for every provider: a world- or
    group-readable key file is a hard error, never a silent downgrade.
    """
    if not path.exists():return None
    s=path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o077:
        raise ValueError('Secret file must be a regular owner-only file (mode 600)')
    return path.read_text().strip() or None
def key():
    value=os.environ.get('META_API_KEY') or os.environ.get('MODEL_API_KEY')
    if value:return value
    return _owner_only(secret_path())
def baseten_key():
    """Baseten key: env BASETEN_API_KEY, else the owner-only local file.

    Deliberately separate from key(): a Meta credential is never reused for a
    Baseten request, and no Mixdog config or keychain is consulted.
    """
    value=os.environ.get('BASETEN_API_KEY')
    if value:return value
    return _owner_only(baseten_secret_path())

def modal_shared_key():
    """Modal Shared bearer: env WB_MODAL_SHARED_API_KEY, else the owner-only file.

    The Modal Shared endpoint uses one bearer string of the documented form
    ``wk-<proxy-token-id>.ws-<proxy-token-secret>``. It is a third container,
    separate from the Meta key and from the legacy Modal proxy token pair, so
    a credential is never reused across routes.
    """
    value=os.environ.get('WB_MODAL_SHARED_API_KEY')
    if value:return value
    return _owner_only(modal_shared_secret_path())

def modal_shared_key_present():
    """Presence-only readiness check: the secret value is never read here."""
    if os.environ.get('WB_MODAL_SHARED_API_KEY'):return True
    try:s=modal_shared_secret_path().lstat()
    except OSError:return False
    return bool(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode&0o077 and s.st_size>0)

def openai_key():
    """OpenAI bearer: env OPENAI_API_KEY, else the owner-only local file.

    A fourth isolated container: a Meta, Baseten or Modal Shared credential is
    never presented to api.openai.com, and no keychain or app config is read.
    """
    value=os.environ.get('OPENAI_API_KEY')
    if value:return value
    return _owner_only(openai_secret_path())

def openai_key_present():
    """Presence-only readiness check: the secret value is never read here."""
    if os.environ.get('OPENAI_API_KEY'):return True
    try:s=openai_secret_path().lstat()
    except OSError:return False
    return bool(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode&0o077 and s.st_size>0)

#: Provider id -> (key file, environment variable names that override it).
PROVIDER_SECRETS={'meta':(secret_path,('META_API_KEY','MODEL_API_KEY')),
                  'baseten':(baseten_secret_path,('BASETEN_API_KEY',)),
                  'modal_shared':(modal_shared_secret_path,('WB_MODAL_SHARED_API_KEY',)),
                  'openai':(openai_secret_path,('OPENAI_API_KEY',))}
READERS={'meta':key,'baseten':baseten_key,'modal_shared':modal_shared_key,'openai':openai_key}
def provider_key(provider):
    """The credential for one provider; never another provider's."""
    reader=READERS.get(provider)
    if reader is None:return None
    return reader()
def env_override(provider):
    """Name of the environment variable currently supplying this provider, if any."""
    for name in PROVIDER_SECRETS.get(provider,(None,('',)))[1]:
        if os.environ.get(name):return name
    return None
def save(provider,value):
    """Store one provider key in its own owner-only file.

    Writes only to that provider's fixed path outside the project tree: no
    caller-supplied path, no overwrite of another provider's file, and the
    value is never read back to a client. An environment variable still wins
    over the file, so a process override keeps working.
    """
    entry=PROVIDER_SECRETS.get(provider)
    if entry is None:raise ValueError('Unknown provider; no key file exists for it')
    if not isinstance(value,str) or not value or value!=value.strip() or any(c.isspace() for c in value):
        raise ValueError('Key must be nonempty and contain no whitespace')
    path=entry[0]()
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    # O_NOFOLLOW + replace-by-rename would still let a symlink race in; opening
    # the fixed path with O_CREAT|O_TRUNC after an lstat check is enough for a
    # single-user localhost app, and the mode is set explicitly so a world-
    # readable file can never be produced by a permissive umask.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    try:
        os.fchmod(fd,0o600)
        os.write(fd,value.encode())
    finally:
        os.close(fd)
    return path
def present(provider):
    """Whether a credential exists for this provider, without revealing it."""
    return bool(provider_key(provider))
