"""Rebuild the 24 locally verified board art templates from user-supplied images."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from PIL import Image

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sources', required=True, type=Path, help='Directory of user-supplied card images named <card_id>_<common|evo>.png')
parser.add_argument('--output', required=True, type=Path, help='New or empty destination directory')
parser.add_argument('--spec', type=Path, default=Path(__file__).with_name('card-template-spec.json'))
args = parser.parse_args()
spec = json.loads(args.spec.read_text(encoding='utf-8'))
if spec['version'] != 1 or spec['resample'] != 'BICUBIC':
    raise SystemExit('Unsupported spec')
if not args.sources.is_dir():
    raise SystemExit('Source directory not found')
if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
    raise SystemExit('Output must be a new or empty directory; no files overwritten')
if args.output.resolve() == args.sources.resolve():
    raise SystemExit('Output and source must differ')
missing = []
for item in spec['verified']:
    if not re.fullmatch(r'\d+_(?:common|evo)\.png', item['input_file']):
        raise SystemExit('Invalid filename in spec')
    p = args.sources / item['input_file']
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != item['source_sha256']:
        missing.append(item['input_file'])
if missing:
    print('Missing or different source images (no output generated):')
    for name in missing: print(name)
    raise SystemExit(1)
images = []
for item in spec['verified']:
    with Image.open(args.sources / item['input_file']) as image:
        image = image.crop(tuple(spec['crop'])).convert('RGB').resize(tuple(spec['size']), Image.Resampling.BICUBIC)
        if hashlib.sha256(image.tobytes()).hexdigest() != item['rgb_sha256']:
            raise SystemExit(f"Pixel mismatch: {item['input_file']}")
        images.append((item['input_file'], image))
args.output.mkdir(parents=True, exist_ok=True)
for filename, image in images:
    image.save(args.output / filename, format='PNG')
print(f"Verified {len(spec['verified'])} board-art templates. {len(spec['without_reference'])} other recorded sources have no reference and were not generated.")
