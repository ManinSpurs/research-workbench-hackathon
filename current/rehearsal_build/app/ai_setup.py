"""Interactive key setup. Does not contact Meta or activate cloud inference."""
from getpass import getpass
import os
from .ai.secrets import secret_path

def main():
    path=secret_path()
    if path.exists():
        print('A key file already exists. No overwrite performed. Use META_API_KEY to override for one process.')
        return
    value=getpass('Meta Model API key (hidden): ').strip()
    if not value or any(c.isspace() for c in value):raise SystemExit('Key must be nonempty and contain no whitespace')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:out.write(value)
    print('Saved in the current macOS user private application folder. Cloud remains off. No API request was sent.')
if __name__=='__main__':main()
