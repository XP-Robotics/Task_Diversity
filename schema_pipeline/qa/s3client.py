"""Minimal pure-stdlib S3 client (AWS SigV4), for cloud-direct QA.

I/O module. No third-party packages -- signing is hmac/hashlib, transport is
urllib. Works against any S3-compatible endpoint (tested on Vultr Object
Storage). Credentials and endpoint come from the environment, never hardcoded:

    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY   (required)
    S3_ENDPOINT        host, e.g. blr2.vultrobjects.com   (required)
    AWS_REGION         e.g. blr2                          (default: us-east-1)
    AWS_S3_BUCKET_NAME default bucket when a path omits it

Only read operations are implemented (list / get / HEAD / presign).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, Iterator, List, Optional, Tuple

_EMPTY_SHA = hashlib.sha256(b"").hexdigest()
_SERVICE = "s3"


class S3Error(RuntimeError):
    pass


class S3Client:
    def __init__(self, endpoint: str, region: str, access_key: str, secret_key: str,
                 default_bucket: Optional[str] = None):
        self.host = endpoint.replace("https://", "").replace("http://", "").strip("/")
        self.region = region
        self.ak = access_key
        self.sk = secret_key
        self.default_bucket = default_bucket

    # -- construction --------------------------------------------------------
    @classmethod
    def from_env(cls) -> "S3Client":
        ak = os.environ.get("AWS_ACCESS_KEY_ID")
        sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
        endpoint = os.environ.get("S3_ENDPOINT")
        region = os.environ.get("AWS_REGION", "us-east-1")
        bucket = os.environ.get("AWS_S3_BUCKET_NAME")
        missing = [n for n, v in (("AWS_ACCESS_KEY_ID", ak),
                                  ("AWS_SECRET_ACCESS_KEY", sk),
                                  ("S3_ENDPOINT", endpoint)) if not v]
        if missing:
            raise S3Error("missing S3 environment variables: " + ", ".join(missing))
        return cls(endpoint, region, ak, sk, bucket)

    # -- signing -------------------------------------------------------------
    def _signing_key(self, datestamp: str) -> bytes:
        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()
        k = sign(("AWS4" + self.sk).encode(), datestamp)
        k = sign(k, self.region)
        k = sign(k, _SERVICE)
        return sign(k, "aws4_request")

    def _now(self) -> Tuple[str, str]:
        now = datetime.datetime.now(datetime.timezone.utc)
        return now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")

    def _authed_request(self, method: str, uri: str, query: Dict[str, str]) -> urllib.request.Request:
        amzdate, datestamp = self._now()
        canon_qs = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
            for k, v in sorted(query.items()))
        canon_headers = f"host:{self.host}\nx-amz-content-sha256:{_EMPTY_SHA}\nx-amz-date:{amzdate}\n"
        signed = "host;x-amz-content-sha256;x-amz-date"
        canon_req = "\n".join([method, uri, canon_qs, canon_headers, signed, _EMPTY_SHA])
        scope = f"{datestamp}/{self.region}/{_SERVICE}/aws4_request"
        sts = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                         hashlib.sha256(canon_req.encode()).hexdigest()])
        sig = hmac.new(self._signing_key(datestamp), sts.encode(), hashlib.sha256).hexdigest()
        auth = (f"AWS4-HMAC-SHA256 Credential={self.ak}/{scope}, "
                f"SignedHeaders={signed}, Signature={sig}")
        url = f"https://{self.host}{uri}"
        if canon_qs:
            url += "?" + canon_qs
        return urllib.request.Request(url, method=method, headers={
            "Host": self.host, "x-amz-date": amzdate,
            "x-amz-content-sha256": _EMPTY_SHA, "Authorization": auth})

    # -- operations ----------------------------------------------------------
    def _bucket(self, bucket: Optional[str]) -> str:
        b = bucket or self.default_bucket
        if not b:
            raise S3Error("no bucket given and AWS_S3_BUCKET_NAME not set")
        return b

    def get_bytes(self, bucket: str, key: str, timeout: int = 120) -> bytes:
        uri = "/" + self._bucket(bucket) + "/" + urllib.parse.quote(key)
        req = self._authed_request("GET", uri, {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def head_size(self, bucket: str, key: str, timeout: int = 60) -> Optional[int]:
        uri = "/" + self._bucket(bucket) + "/" + urllib.parse.quote(key)
        req = self._authed_request("HEAD", uri, {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                cl = r.headers.get("Content-Length")
                return int(cl) if cl is not None else None
        except Exception:
            return None

    def presign(self, bucket: str, key: str, expires: int = 3600) -> str:
        amzdate, datestamp = self._now()
        b = self._bucket(bucket)
        uri = "/" + b + "/" + urllib.parse.quote(key)
        scope = f"{datestamp}/{self.region}/{_SERVICE}/aws4_request"
        q = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.ak}/{scope}",
            "X-Amz-Date": amzdate,
            "X-Amz-Expires": str(expires),
            "X-Amz-SignedHeaders": "host",
        }
        canon_qs = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
            for k, v in sorted(q.items()))
        canon_req = "\n".join(["GET", uri, canon_qs, f"host:{self.host}\n",
                               "host", "UNSIGNED-PAYLOAD"])
        sts = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                         hashlib.sha256(canon_req.encode()).hexdigest()])
        sig = hmac.new(self._signing_key(datestamp), sts.encode(), hashlib.sha256).hexdigest()
        return f"https://{self.host}{uri}?{canon_qs}&X-Amz-Signature={sig}"

    def _ns(self, root: ET.Element) -> Dict[str, str]:
        return {"s3": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}

    def _list_page(self, bucket: str, query: Dict[str, str], retries: int = 4) -> ET.Element:
        """One listing request, parsed to XML, retrying transient read errors."""
        last = None
        for attempt in range(retries):
            try:
                req = self._authed_request("GET", "/" + bucket, query)
                with urllib.request.urlopen(req, timeout=120) as r:
                    return ET.fromstring(r.read())
            except Exception as exc:  # noqa: BLE001 -- IncompleteRead/URLError/timeout
                last = exc
        raise S3Error(f"list failed after {retries} attempts: {last}")

    def list_objects(self, bucket: str, prefix: str = "",
                     delimiter: Optional[str] = None) -> Iterator[Tuple[str, int]]:
        """Yield (key, size), following continuation tokens (paginated)."""
        b = self._bucket(bucket)
        token = None
        while True:
            q = {"list-type": "2", "max-keys": "1000", "prefix": prefix}
            if delimiter:
                q["delimiter"] = delimiter
            if token:
                q["continuation-token"] = token
            root = self._list_page(b, q)
            ns = self._ns(root)
            fa = (lambda e, n: e.findall(f"s3:{n}", ns)) if ns else (lambda e, n: e.findall(n))
            f1 = (lambda e, n: e.find(f"s3:{n}", ns)) if ns else (lambda e, n: e.find(n))
            for c in fa(root, "Contents"):
                yield f1(c, "Key").text, int(f1(c, "Size").text)
            trunc = f1(root, "IsTruncated")
            if trunc is None or trunc.text != "true":
                break
            nxt = f1(root, "NextContinuationToken")
            token = nxt.text if nxt is not None else None
            if not token:
                break

    def list_prefixes(self, bucket: str, prefix: str = "", delimiter: str = "/") -> List[str]:
        """All CommonPrefixes (folders) under ``prefix``, following continuation
        tokens so the whole dataset is enumerated, not just the first page."""
        b = self._bucket(bucket)
        out: List[str] = []
        token = None
        while True:
            q = {"list-type": "2", "max-keys": "1000", "prefix": prefix, "delimiter": delimiter}
            if token:
                q["continuation-token"] = token
            root = self._list_page(b, q)
            ns = self._ns(root)
            fa = (lambda e, n: e.findall(f"s3:{n}", ns)) if ns else (lambda e, n: e.findall(n))
            f1 = (lambda e, n: e.find(f"s3:{n}", ns)) if ns else (lambda e, n: e.find(n))
            out.extend(f1(p, "Prefix").text for p in fa(root, "CommonPrefixes"))
            trunc = f1(root, "IsTruncated")
            if trunc is None or trunc.text != "true":
                break
            nxt = f1(root, "NextContinuationToken")
            token = nxt.text if nxt is not None else None
            if not token:
                break
        return out


def parse_s3_url(url: str, default_bucket: Optional[str]) -> Tuple[str, str]:
    """Split ``s3://bucket/prefix`` (or ``s3://prefix`` with a default bucket)."""
    rest = url[len("s3://"):]
    if "/" in rest:
        head, tail = rest.split("/", 1)
    else:
        head, tail = rest, ""
    # If the first segment matches the default bucket, treat it as the bucket;
    # otherwise, when a default bucket exists, treat the whole rest as a prefix.
    if default_bucket and head != default_bucket:
        return default_bucket, rest
    return head, tail
