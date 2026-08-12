#!/usr/bin/env python3
"""Upload the built page to SmarterASP.NET over FTP.

Credentials come from the environment, never from the repo:
  FTP_HOST      e.g. ftp.yoursite.com   (Control Panel > FTP Manager)
  FTP_USER
  FTP_PASS
  FTP_DIR       remote directory, /CarsScrapping
  FTP_TLS       "0" to force plain FTP; default is to try FTPS first

Uploads to a temporary name and renames into place, so a visitor mid-upload
never sees a half-written page.
"""
#!/usr/bin/env python3
"""Upload the built page to SmarterASP.NET over FTP."""
import os, sys, time
from ftplib import FTP, FTP_TLS, error_perm, error_temp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.dirname(ROOT)

FILES = [
    (os.path.join(OUT, 'kei_cars_islamabad_rawalpindi.html'), 'index.html'),
    (os.path.join(OUT, '660cc_kei_cars_isb_rwp.xlsx'), '660cc_kei_cars_isb_rwp.xlsx'),
]

def connect():
    host = os.environ['FTP_HOST']
    user = os.environ['FTP_USER']
    password = os.environ['FTP_PASS']
    
    if os.environ.get('FTP_TLS', '1') != '0':
        try:
            ftp = FTP_TLS(host, timeout=180)  # 3 minutes instead of 1
            ftp.login(user, password)
            ftp.prot_p()
            print('✓ Connected over FTPS')
            return ftp
        except Exception as e:
            print(f'FTPS failed ({e}); falling back to plain FTP')
    
    ftp = FTP(host, timeout=180)
    ftp.login(user, password)
    print('✓ Connected over plain FTP')
    return ftp

def main():
    missing = [p for p, _ in FILES if not os.path.exists(p)]
    if missing:
        print(f'ABORT: build output missing: {missing}', file=sys.stderr)
        return 1
    
    ftp = connect()
    try:
        remote_dir = os.environ.get('FTP_DIR', '/')
        if remote_dir != '/':
            try:
                ftp.cwd(remote_dir)
            except error_perm as e:
                print(f'ERROR: Cannot access {remote_dir}: {e}', file=sys.stderr)
                return 1
        
        for local, remote in FILES:
            tmp = remote + '.uploading'
            max_retries = 3
            
            for attempt in range(1, max_retries + 1):
                try:
                    print(f'Uploading {os.path.basename(local)} (attempt {attempt}/{max_retries})...')
                    with open(local, 'rb') as fh:
                        ftp.storbinary(f'STOR {tmp}', fh, blocksize=64 * 1024)
                    
                    # Delete old version if it exists
                    try:
                        ftp.delete(remote)
                    except error_perm:
                        pass
                    
                    # Rename into place
                    ftp.rename(tmp, remote)
                    print(f'✓ {remote_dir}/{remote} ({os.path.getsize(local):,} bytes)')
                    break  # Success, move to next file
                
                except (error_perm, error_temp, TimeoutError, OSError) as e:
                    print(f'  Attempt {attempt} failed: {e}', file=sys.stderr)
                    if attempt < max_retries:
                        print(f'  Retrying in 5 seconds...', file=sys.stderr)
                        time.sleep(5)
                        # Reconnect on timeout
                        try:
                            ftp.quit()
                        except:
                            ftp.close()
                        ftp = connect()
                    else:
                        print(f'ERROR: Failed to upload {remote} after {max_retries} attempts', file=sys.stderr)
                        return 1
    
    finally:
        try:
            ftp.quit()
        except:
            ftp.close()
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
