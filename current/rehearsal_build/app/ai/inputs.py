"""Local input registration and byte-level format checks. No external fetch or upload."""
import base64,hashlib,io,json,wave
from pathlib import PurePath
MAX_BYTES=8*1024*1024
MIMES={'image/png':'image','image/jpeg':'image','image/webp':'image','application/pdf':'pdf',
       'video/mp4':'video','audio/wav':'audio','text/plain':'text'}

def inspect(name,mime,encoded):
    if not isinstance(name,str) or PurePath(name).name!=name or not 1<=len(name)<=120 or any(ord(c)<32 for c in name):raise ValueError('Provide a plain filename')
    if mime not in MIMES:raise ValueError('Unsupported local media format')
    if not isinstance(encoded,str) or len(encoded)>MAX_BYTES*4//3+8:raise ValueError('Asset exceeds 8 MiB')
    try:raw=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError) as exc:raise ValueError('Invalid base64') from exc
    if not 0<len(raw)<=MAX_BYTES:raise ValueError('Asset must be nonempty and at most 8 MiB')
    ok=(mime=='image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n') or
        mime=='image/jpeg' and raw.startswith(b'\xff\xd8\xff') or
        mime=='image/webp' and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP' or
        mime=='application/pdf' and raw.startswith(b'%PDF-') or
        mime=='video/mp4' and raw[4:8]==b'ftyp' or
        mime=='audio/wav' and raw[:4]==b'RIFF' and raw[8:12]==b'WAVE' or mime=='text/plain')
    if not ok:raise ValueError('File bytes do not match declared media format')
    meta={'name':name,'mime':mime,'kind':MIMES[mime],'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    if mime=='audio/wav':
        try:
            with wave.open(io.BytesIO(raw)) as wav:
                if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or wav.getframerate() not in (16000,24000) or wav.getcomptype()!='NONE':raise ValueError('Voice requires mono 16-bit PCM WAV at 16 or 24 kHz')
                meta['duration_seconds']=wav.getnframes()/wav.getframerate()
        except wave.Error as exc:raise ValueError('Invalid WAV') from exc
    if mime=='text/plain':
        try:raw.decode('utf-8')
        except UnicodeError as exc:raise ValueError('Text input must be UTF-8') from exc
    return raw,meta

def block(meta,encoded):
    url=f"data:{meta['mime']};base64,{encoded}"
    if meta['kind']=='image':return {'type':'input_image','image_url':url}
    if meta['kind']=='pdf':return {'type':'input_file','filename':meta['name'],'file_data':url}
    if meta['kind']=='video':return {'type':'input_video','video_url':url}
    if meta['kind']=='text':return {'type':'input_text','text':base64.b64decode(encoded).decode('utf-8')}
    raise ValueError('Audio is accepted by the typed Voice transcription adapter, not text reasoning in this revision')
