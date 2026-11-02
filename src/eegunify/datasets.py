"""Fetch public EEG data for demos, real-data tests and the validation harness.

Two sources:

* **ERP CORE** (Kappenman et al., 2021; CC-BY-4.0), BIDS version on OSF. The archive is 1.9 GB
  and holds 5 subjects x 7 components; only the files actually needed are pulled out of it with
  HTTP range requests (the zip's central directory is read remotely), about 85 MB per subject
  and component.
* **OpenNeuro** snapshots: one subject's EEG files, listed through the OpenNeuro GraphQL API.
  Snapshot URLs carry an S3 ``versionId``, so a pinned snapshot tag always gives the same bytes.

* **ERP CORE per-component OSF projects** (``fetch_erpcore_full``): every subject (1-40) of a
  component, raw ``<n>_<comp>.set/.fdt`` only (about 98 MB per subject for N170), listed
  through the OSF API at fetch time. Used by M-E6b (docs/BLUEPRINT_PHASES.md, OQ1).

* **Zenodo** records (``fetch_zenodo``): chosen files of a record, checked against the MD5 the
  record lists (GABA infant VEP: record 5172962).

Every download is recorded with its sha256 in ``<dest>/eegunify_manifest.json``; files already
present with the recorded hash are not downloaded again. The downloaded data are never edited.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

#: ERP CORE BIDS archive (OSF file 3zk6n, version 2, as used by the MNE-BIDS-Pipeline).
ERPCORE_URL = (
    "https://files.osf.io/v1/resources/rxvq7/providers/osfstorage/"
    "6062230222950302146ec3c9?version=2"
)
ERPCORE_SIZE = 1_887_757_240
ERPCORE_SHA256 = "ddc94a7c9ba1922637f2770592dd51c019d341bf6bc8558e663e1979a4cb002f"
ERPCORE_COMPONENTS = ("N170", "P3", "N2pc", "MMN", "N400", "ERN", "LRP")
ERPCORE_SUBJECTS = ("015", "016", "017", "018", "019")

OPENNEURO_GRAPHQL = "https://openneuro.org/crn/graphql"
USER_AGENT = "eegunify (https://github.com/StefanoGaliano/eeg-erp-pipeline)"


class TooLargeError(ValueError):
    """The requested subject is larger than the caller's size limit."""


def _retry(fn: Callable[[], T], what: str, attempts: int = 5) -> T:
    """Call ``fn`` with exponential backoff on network errors (OSF/S3 time out occasionally)."""
    for i in range(attempts):
        try:
            return fn()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            # 4xx is permanent (retrying a dead link only wastes time), except 429 rate limiting
            # and 403: OSF's file server sometimes answers bursts of requests for public files
            # with 403 (seen 2026-11-01, archive range requests and single files alike).
            permanent = (
                isinstance(err, urllib.error.HTTPError)
                and 400 <= err.code < 500
                and err.code not in (403, 429)
            )
            if permanent or i == attempts - 1:
                raise
            wait = 2**i
            log.warning("%s failed (%s); retrying in %ss", what, err, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


class HTTPRangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file using HTTP ``Range`` requests."""

    def __init__(self, url: str, size: int, timeout: float = 120.0) -> None:
        self.url, self.size, self.timeout = url, size, timeout
        self.pos = 0
        self.requests = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = base + offset
        return self.pos

    def readinto(self, buffer: Any) -> int:
        if self.pos >= self.size:
            return 0
        end = min(self.pos + len(buffer), self.size) - 1
        start = self.pos

        def get() -> bytes:
            req = urllib.request.Request(
                self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 206:
                    raise ConnectionError(f"server ignored the Range header ({resp.status})")
                data: bytes = resp.read()
                return data

        data = _retry(get, f"bytes {start}-{end}")
        self.requests += 1
        buffer[: len(data)] = data
        self.pos += len(data)
        return len(data)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


@dataclass
class Manifest:
    """``eegunify_manifest.json``: what was fetched from where, with hashes."""

    path: Path
    entries: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, dest: Path) -> Manifest:
        p = dest / "eegunify_manifest.json"
        entries = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return cls(p, entries)

    def is_current(self, dest: Path, rel: str) -> bool:
        entry = self.entries.get(rel)
        target = dest / rel
        return bool(entry and target.exists() and sha256(target) == entry["sha256"])

    def record(self, dest: Path, rel: str, source: str) -> None:
        target = dest / rel
        self.entries[rel] = {
            "sha256": sha256(target),
            "bytes": target.stat().st_size,
            "source": source,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.entries, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )


def _erpcore_members(names: Iterable[str], component: str, subjects: Iterable[str]) -> list[str]:
    wanted = []
    subs = {f"sub-{s}" for s in subjects}
    for name in names:
        if name.endswith("/"):
            continue
        parts = name.split("/")
        top_level = len(parts) == 2 and (
            not parts[1].startswith("task-") or parts[1].startswith(f"task-{component}_")
        )
        subject_file = len(parts) > 2 and parts[1] in subs and parts[2] == f"ses-{component}"
        if top_level or subject_file:
            wanted.append(name)
    return wanted


def fetch_erpcore(
    dest: Path, component: str = "N170", subjects: Iterable[str] = ("015",)
) -> list[Path]:
    """Extract one ERP CORE component for the given subjects into ``dest/ERP_CORE``.

    Returns the paths of the subjects' EEG entry files (``*_eeg.set``).
    """
    if component not in ERPCORE_COMPONENTS:
        raise ValueError(f"unknown ERP CORE component {component!r}; choose {ERPCORE_COMPONENTS}")
    subjects = list(subjects)
    bad = set(subjects) - set(ERPCORE_SUBJECTS)
    if bad:
        raise ValueError(f"subjects {sorted(bad)} not in the archive {ERPCORE_SUBJECTS}")
    dest.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load(dest)
    entry_files = [
        dest / "ERP_CORE" / f"sub-{s}" / f"ses-{component}" / "eeg" / f"sub-{s}_ses-{component}"
        f"_task-{component}_eeg.set"
        for s in subjects
    ]
    if _erpcore_complete(manifest, dest, component, subjects):
        log.info("ERP CORE %s already present for %s", component, subjects)
        return entry_files
    remote = HTTPRangeFile(ERPCORE_URL, ERPCORE_SIZE)
    with zipfile.ZipFile(io.BufferedReader(remote, buffer_size=1 << 22)) as archive:
        for member in _erpcore_members(archive.namelist(), component, subjects):
            if manifest.is_current(dest, member):
                continue
            log.info("fetching %s", member)
            target = dest / member
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            # zipfile verifies each member's CRC-32 while reading.
            with archive.open(member) as src, tmp.open("wb") as out:
                while block := src.read(1 << 20):
                    out.write(block)
            tmp.replace(target)
            manifest.record(dest, member, f"osf:3zk6n@v2:{member}")
    log.info("ERP CORE: %d HTTP range requests", remote.requests)
    return entry_files


#: OSF projects of the ERP CORE components (the per-component "All Data and Scripts" folders).
ERPCORE_FULL_NODES = {"N170": "pfde9"}
OSF_API = "https://api.osf.io/v2"


def _osf_list(url: str) -> list[dict[str, Any]]:
    """Every entry of an OSF API listing (follows pagination)."""
    out: list[dict[str, Any]] = []
    next_url: str | None = url

    def get(u: str) -> dict[str, Any]:
        req = urllib.request.Request(u, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data: dict[str, Any] = json.load(resp)
            return data

    while next_url:
        page = _retry(functools.partial(get, next_url), next_url)
        out += page["data"]
        next_url = page["links"].get("next")
    return out


def _osf_children(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return _osf_list(entry["relationships"]["files"]["links"]["related"]["href"])


def _stream(url: str, target: Path, size: int) -> None:
    """Download ``url`` to ``target`` in blocks (via ``.part``), checking the byte count."""
    tmp = target.with_name(target.name + ".part")

    def get() -> None:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as out:
            while block := resp.read(1 << 22):
                out.write(block)
        got = tmp.stat().st_size
        if got != size:
            raise ConnectionError(f"{target.name}: got {got} bytes, OSF lists {size}")

    _retry(get, url)
    tmp.replace(target)


def fetch_erpcore_full(
    dest: Path, component: str = "N170", subjects: Iterable[str] | None = None
) -> list[Path]:
    """Raw recordings of ERP CORE subjects from the component's OSF project.

    Files land in ``dest/erpcore_full/<component>/<n>/<n>_<component>.set`` (and ``.fdt``),
    named as on OSF (the ``.set`` names its ``.fdt``); configs reach them with
    ``recordings.pattern: "{subject_int}/{subject_int}_<component>.set"``. ``subjects`` are
    labels such as ``015`` (default: all 40). Returns the ``.set`` paths.
    """
    if component not in ERPCORE_FULL_NODES:
        raise ValueError(
            f"no full-data project known for {component!r}: {list(ERPCORE_FULL_NODES)}"
        )
    wanted = [f"{int(s):03d}" for s in (subjects or [str(i) for i in range(1, 41)])]
    bad = [s for s in wanted if not 1 <= int(s) <= 40]
    if bad:
        raise ValueError(f"ERP CORE subjects are 1-40; got {bad}")
    node = ERPCORE_FULL_NODES[component]
    root = dest / "erpcore_full" / component
    manifest = Manifest.load(dest)
    names = {s: f"{int(s)}/{int(s)}_{component}" for s in wanted}
    rels = {
        s: [f"erpcore_full/{component}/{names[s]}{ext}" for ext in (".set", ".fdt")] for s in wanted
    }
    todo = [s for s in wanted if not all(manifest.is_current(dest, r) for r in rels[s])]
    if todo:
        listing = _osf_list(f"{OSF_API}/nodes/{node}/files/osfstorage/")
        top = next(
            e for e in listing if e["attributes"]["name"] == f"{component} All Data and Scripts"
        )
        folders = {e["attributes"]["name"]: e for e in _osf_children(top)}
        for s in todo:
            n = str(int(s))
            files = {e["attributes"]["name"]: e for e in _osf_children(folders[n])}
            for rel in rels[s]:
                name = rel.rsplit("/", 1)[1]
                entry = files[name]
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                log.info("fetching %s (%d MB)", rel, entry["attributes"]["size"] // 2**20)
                _stream(entry["links"]["download"], target, int(entry["attributes"]["size"]))
                manifest.record(dest, rel, f"osf:{node}/{entry['id']}")
    return [root / f"{names[s]}.set" for s in wanted]


ZENODO_API = "https://zenodo.org/api/records/{record}"


def _md5(path: Path) -> str:
    h = hashlib.md5()  # Zenodo publishes MD5 checksums; the manifest keeps sha256
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def fetch_zenodo(
    record: str, dest: Path, files: Iterable[str] | None = None, suffixes: Iterable[str] = ()
) -> list[Path]:
    """Download files of a public Zenodo record into ``dest/zenodo<record>/``.

    ``files`` names them exactly; otherwise every file whose name ends with one of ``suffixes``
    (all files when both are empty). Each file is checked against the record's MD5 and recorded
    in the manifest; files already present with the recorded hash are skipped.
    """
    req = urllib.request.Request(
        ZENODO_API.format(record=record), headers={"User-Agent": USER_AGENT}
    )

    def get() -> dict[str, Any]:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data: dict[str, Any] = json.load(resp)
            return data

    meta = _retry(get, f"zenodo record {record}")
    entries = {f["key"]: f for f in meta["files"]}
    wanted = (
        list(files)
        if files
        else [k for k in sorted(entries) if not tuple(suffixes) or k.endswith(tuple(suffixes))]
    )
    unknown = sorted(set(wanted) - set(entries))
    if unknown:
        raise ValueError(f"zenodo record {record} has no files {unknown}; it has {sorted(entries)}")
    manifest = Manifest.load(dest)
    out = []
    for name in wanted:
        rel = f"zenodo{record}/{name}"
        target = dest / rel
        out.append(target)
        if manifest.is_current(dest, rel):
            continue
        entry = entries[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        log.info("fetching %s (%d MB)", rel, entry["size"] // 2**20)
        _stream(entry["links"]["self"], target, int(entry["size"]))
        algo, _, expected = entry["checksum"].partition(":")
        if algo == "md5" and _md5(target) != expected:
            target.unlink()
            raise ConnectionError(f"{rel}: MD5 differs from the Zenodo record")
        manifest.record(dest, rel, f"zenodo:{record}/{name}")
    return out


def _erpcore_complete(manifest: Manifest, dest: Path, component: str, subjects: list[str]) -> bool:
    """True if every needed file was fetched before and is unchanged (no network needed)."""
    for s in subjects:
        prefix = f"ERP_CORE/sub-{s}/ses-{component}/"
        rels = [r for r in manifest.entries if r.startswith(prefix)]
        if not any(r.endswith("_eeg.set") for r in rels):
            return False
        if not all(manifest.is_current(dest, r) for r in rels):
            return False
    return manifest.is_current(dest, f"ERP_CORE/task-{component}_events.json")


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    def call() -> dict[str, Any]:
        req = urllib.request.Request(
            OPENNEURO_GRAPHQL,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body: dict[str, Any] = json.load(resp)
        if body.get("errors"):
            raise RuntimeError(f"OpenNeuro API error: {body['errors'][0].get('message')}")
        return body["data"]  # type: ignore[no-any-return]

    return _retry(call, "OpenNeuro API")


_FILES_Q = (
    "query($id:ID!,$tag:String!,$tree:String){snapshot(datasetId:$id,tag:$tag)"
    "{files(tree:$tree){filename directory id size urls}}}"
)
_TAG_Q = "query($id:ID!){dataset(id:$id){latestSnapshot{tag}}}"


def openneuro_latest_tag(dataset: str) -> str:
    return str(_graphql(_TAG_Q, {"id": dataset})["dataset"]["latestSnapshot"]["tag"])


def _list(dataset: str, tag: str, tree: str | None) -> list[dict[str, Any]]:
    data = _graphql(_FILES_Q, {"id": dataset, "tag": tag, "tree": tree})
    return list(data["snapshot"]["files"])


_ENTRY_SUFFIXES = (".vhdr", ".bdf", ".edf", ".set", ".xdf", ".gdf", ".cnt", ".cdt", ".mff")


def one_recording(files: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """Keep the first recording of a subject: its entry file, its companions and sidecars
    (same ``sub-.._task-.._run-..`` prefix), subject-level files without a task, and the
    dataset's top-level files."""
    entries = sorted(r for r, _, _ in files if r.lower().endswith(_ENTRY_SUFFIXES))
    if not entries:
        return files
    base = entries[0].rsplit("/", 1)[-1].rsplit("_", 1)[0]
    keep = []
    for rel, size, url in files:
        name = rel.rsplit("/", 1)[-1]
        if "/" not in rel or name.startswith(base + "_") or name.startswith(base + "."):
            keep.append((rel, size, url))
        elif "_task-" not in name and not name.lower().endswith(_ENTRY_SUFFIXES):
            keep.append((rel, size, url))  # e.g. sub-01_electrodes.tsv, sub-01_scans.tsv
    return keep


def openneuro_subject_files(
    dataset: str,
    subject: str | None = None,
    tag: str | None = None,
    *,
    first_recording_only: bool = False,
) -> tuple[str, str, list[tuple[str, int, str]]]:
    """List ``(relative path, bytes, url)`` of one subject's ``eeg/`` files plus top-level files.

    ``subject`` defaults to the first ``sub-*`` folder; ``tag`` to the latest snapshot.
    Returns ``(tag, subject, files)``.
    """
    tag = tag or openneuro_latest_tag(dataset)
    top = _list(dataset, tag, None)
    subs = sorted(f["filename"] for f in top if f["directory"] and f["filename"].startswith("sub-"))
    if not subs:
        raise RuntimeError(f"{dataset}@{tag} has no sub-* folders")
    sub = f"sub-{subject}" if subject else subs[0]
    if sub not in subs:
        raise RuntimeError(f"{dataset}@{tag} has no {sub}")
    files = [
        (f["filename"], f["size"], f["urls"][0]) for f in top if not f["directory"] and f["urls"]
    ]

    def walk(node_id: str, prefix: str) -> None:
        for f in _list(dataset, tag, node_id):
            rel = f"{prefix}/{f['filename']}"
            if f["directory"]:
                # Recurse into sessions and the eeg datatype only.
                name = f["filename"]
                if name.startswith("ses-") or name == "eeg" or name.endswith(".mff"):
                    walk(f["id"], rel)
            elif f["urls"] and ("/eeg/" in rel or rel.endswith("_scans.tsv")):
                files.append((rel, f["size"], f["urls"][0]))

    sub_node = next(f for f in top if f["filename"] == sub)
    walk(sub_node["id"], sub)
    if first_recording_only:
        files = one_recording(files)
    return tag, sub.removeprefix("sub-"), files


def fetch_openneuro(
    dataset: str,
    dest: Path,
    subject: str | None = None,
    tag: str | None = None,
    max_bytes: int | None = None,
    first_recording_only: bool = False,
) -> tuple[str, str, list[Path]]:
    """Download one subject of an OpenNeuro dataset into ``dest/<dataset>``.

    With ``first_recording_only``, only that subject's first recording (plus sidecars) is
    fetched instead of every task and run.

    Raises
    ------
    TooLargeError
        If the subject's files exceed ``max_bytes`` (nothing is downloaded).
    """
    tag, subject, files = openneuro_subject_files(
        dataset, subject, tag, first_recording_only=first_recording_only
    )
    total = sum(size for _, size, _ in files)
    if max_bytes is not None and total > max_bytes:
        raise TooLargeError(
            f"{dataset} sub-{subject}: {total / 1e6:.0f} MB exceeds the "
            f"{max_bytes / 1e6:.0f} MB limit"
        )
    root = dest / dataset
    manifest = Manifest.load(root)
    out = []
    for rel, _size, url in files:
        target = root / rel
        out.append(target)
        if manifest.is_current(root, rel):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")

        def download(url: str = url, tmp: Path = tmp) -> None:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
                while block := resp.read(1 << 20):
                    fh.write(block)

        log.info("fetching %s/%s", dataset, rel)
        try:
            _retry(download, f"{dataset}/{rel}")
        except urllib.error.HTTPError as err:
            # Top-level files (participants.json, .gitattributes) are optional; OpenNeuro
            # sometimes lists them with dead links (ds003645). Subject files stay mandatory.
            if "/" in rel or err.code != 404:
                raise
            log.warning("%s/%s: %s; skipped (optional top-level file)", dataset, rel, err)
            tmp.unlink(missing_ok=True)
            out.pop()
            continue
        tmp.replace(target)
        manifest.record(root, rel, f"openneuro:{dataset}@{tag}")
    return tag, subject, out


#: mne-testing-data pinned to one commit, so real-file tests always see the same bytes.
MNE_TESTING_COMMIT = "33ef2a6f6345a1adf8764f8f63725f0ed9e4bb92"
_MNE_RAW = "https://raw.githubusercontent.com/mne-tools/mne-testing-data/{commit}/{path}"
_MNE_TREE = "https://api.github.com/repos/mne-tools/mne-testing-data/contents/{path}?ref={commit}"


def _mne_dir_files(path: str) -> list[str]:
    """Files under a directory of mne-testing-data (for EGI ``.mff`` folders)."""

    def get(p: str) -> list[dict[str, Any]]:
        req = urllib.request.Request(
            _MNE_TREE.format(path=urllib.parse.quote(p), commit=MNE_TESTING_COMMIT),
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data: list[dict[str, Any]] = json.load(resp)
            return data

    out: list[str] = []
    for item in _retry(lambda: get(path), f"list {path}"):
        if item["type"] == "dir":
            out.extend(_mne_dir_files(item["path"]))
        else:
            out.append(item["path"])
    return out


def fetch_mne_testing(paths: Iterable[str], dest: Path) -> list[Path]:
    """Download individual files (or ``.mff`` folders) from mne-testing-data.

    ``paths`` are repository paths such as ``"GDF/test_gdf_2.20.gdf"``; a path ending in
    ``.mff`` fetches the whole folder. Returns the local paths in the same order.
    """
    dest.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load(dest)
    out = []
    for path in paths:
        files = _mne_dir_files(path) if path.endswith(".mff") else [path]
        for rel in files:
            if manifest.is_current(dest, rel):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            url = _MNE_RAW.format(commit=MNE_TESTING_COMMIT, path=urllib.parse.quote(rel))

            def download(url: str = url, target: Path = target) -> None:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    target.write_bytes(resp.read())

            log.info("fetching mne-testing-data/%s", rel)
            _retry(download, rel)
            manifest.record(dest, rel, f"mne-testing-data@{MNE_TESTING_COMMIT[:12]}")
        out.append(dest / path)
    return out
