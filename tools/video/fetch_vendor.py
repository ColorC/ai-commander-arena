"""Download the sand table's third-party browser libraries into tools/video/vendor/ (pinned versions, jsDelivr).

    python -I -X utf8 tools/video/fetch_vendor.py

They are not kept in the repo (see NOTICE): three.js 0.170.0 (+ GLTFLoader, SkeletonUtils, BufferGeometryUtils from its
examples/jsm), milsymbol 2.2.0, mp4-muxer 5.1.3 (all MIT) and GSAP 3.15.0 (GreenSock standard no-charge license).
vendor/savepoint/ is this repo's own port (MIT, see its LICENSE) and stays. vendor/fonts.css is made by fetch_fonts.py.
"""
import pathlib
import urllib.request

VENDOR = pathlib.Path(__file__).resolve().parent / "vendor"
CDN = "https://cdn.jsdelivr.net/npm/"
FILES = {
    "three.module.js": "three@0.170.0/build/three.module.js",
    "addons/loaders/GLTFLoader.js": "three@0.170.0/examples/jsm/loaders/GLTFLoader.js",
    "addons/utils/SkeletonUtils.js": "three@0.170.0/examples/jsm/utils/SkeletonUtils.js",
    "addons/utils/BufferGeometryUtils.js": "three@0.170.0/examples/jsm/utils/BufferGeometryUtils.js",
    "milsymbol.js": "milsymbol@2.2.0/dist/milsymbol.js",
    "mp4-muxer.js": "mp4-muxer@5.1.3/build/mp4-muxer.js",
    "gsap.min.js": "gsap@3.15.0/dist/gsap.min.js",
}


def main():
    for rel, src in FILES.items():
        out = VENDOR / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(CDN + src, headers={"User-Agent": "ai-commander-arena/fetch_vendor"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        out.write_bytes(data)
        print(f"{rel}  {len(data):,} bytes  <- {CDN}{src}")


if __name__ == "__main__":
    main()
