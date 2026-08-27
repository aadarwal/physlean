#!/usr/bin/env python3
"""Mode-framed in-memory source channel and view construction for S5.

The S5 oracle audit requires that the process elaborating generated syntax
never holds the bytes it must not read.  Two things follow, and this module
owns both.

CHANNEL.  Sources are never written to a path the child can reach, because a
generated body may run arbitrary metaprograms and both ``include_str`` and
``IO.FS.readFile`` are available to it in core Lean.  Instead each process
receives length-framed payloads on stdin::

    <channel-nonce>\\n
    FRAME <role> <byte-length>\\n
    <byte-length bytes><\\n>
    ...
    ENDFRAMES\\n
    GO:<channel-nonce>\\n
    <EOF>

Frames precede the GO line so the child can prevalidate and emit its start
record before any generated syntax is parsed, while still satisfying the
production requirement that stdin ends immediately after authorization.

VIEWS.  The target process gets ``prefix`` (bytes before the target), the exact
trusted ``header``, and ``target`` (bytes through the retained body end) — never
the original body and never the suffix, so trusted prefix metaprograms cannot
persist future bytes either.  The suffix process gets ``prefix``, ``header``, a
``suffix`` view whose body region alone is masked, and the normalized constant
``bundle``.  The trusted prefix/header remain visible to suffix metaprograms;
masking preserves every byte offset and newline while neither body is present.
"""
from v2b_common import V2BError, sha256_bytes

TARGET_FRAME_ROLES = ("prefix", "header", "target")
SUFFIX_FRAME_ROLES = ("prefix", "header", "suffix", "bundle")
FRAME_CHANNEL_CONTRACT = dict(
    schema="v2b_lean_source_frame_channel_v2",
    transport="child stdin, closed immediately after authorization",
    order="channel nonce line, source frames, ENDFRAMES, GO line, EOF",
    target_roles=list(TARGET_FRAME_ROLES),
    suffix_roles=list(SUFFIX_FRAME_ROLES),
    payload="raw UTF-8 bytes with an explicit byte length and no escaping",
    views=("target process holds [0,targetStart), trusted [0,headerEnd), and "
           "[0,retainedEnd) only; suffix process preserves the trusted header, "
           "masks only [headerEnd,retainedEnd), and appends the trusted suffix"),
    rationale=("generated metaprograms can call include_str and "
               "IO.FS.readFile and can persist anything their FileMap "
               "exposes, so neither the original body nor the suffix is ever "
               "present in the process that elaborates generated syntax"),
)


def _hex(value):
    return isinstance(value, str) and len(value) == 64 \
        and all(char in "0123456789abcdef" for char in value)


_MASK_BY_UTF8_WIDTH = {1: " ", 2: "\u00a0", 3: "\u2007", 4: "\U00010000"}


def blank_region(text):
    """Mask source scalars while preserving bytes, lines, columns and whitespace."""
    out = []
    for char in text:
        if char.isspace():
            out.append(char)
        else:
            out.append(_MASK_BY_UTF8_WIDTH[len(char.encode("utf-8"))])
    blanked = "".join(out)
    if len(blanked.encode("utf-8")) != len(text.encode("utf-8")):
        raise V2BError("blanking changed the byte length of a source region")
    if len(blanked) != len(text):
        raise V2BError("blanking changed the source scalar count")
    return blanked


def build_views(module_text, target_start, header_end, retained_end):
    """Exact per-process views from one reconstruction and its offsets."""
    blob = module_text.encode("utf-8")
    if not 0 <= target_start < header_end < retained_end <= len(blob):
        raise V2BError("target/header/retained byte offsets are out of range")
    for offset in (target_start, header_end, retained_end):
        try:
            blob[:offset].decode("utf-8")
        except UnicodeError as err:
            raise V2BError(f"offset {offset} splits a UTF-8 character: "
                           f"{err}") from err
    prefix_view = blob[:target_start].decode("utf-8")
    header_view = blob[:header_end].decode("utf-8")
    target_view = blob[:retained_end].decode("utf-8")
    suffix_view = (header_view
                   + blank_region(blob[header_end:retained_end].decode("utf-8"))
                   + blob[retained_end:].decode("utf-8"))
    if len(suffix_view.encode("utf-8")) != len(blob):
        raise V2BError("suffix view does not preserve module byte offsets")
    return dict(prefix=prefix_view, header=header_view, target=target_view,
                suffix=suffix_view)


def frame_bytes(role, payload):
    """One exact ``FRAME`` record: header line, payload, terminator."""
    if not isinstance(payload, bytes):
        raise V2BError(f"source frame {role} payload must be bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as err:
        raise V2BError(f"source frame {role} is not valid UTF-8: {err}") \
            from err
    if text.encode("utf-8") != payload:
        raise V2BError(f"source frame {role} does not round-trip UTF-8")
    return (f"FRAME {role} {len(payload)}\n".encode("utf-8") + payload
            + b"\n")


def channel_payload(channel_nonce, sources, roles, authorize=True):
    """Complete stdin payload for one driver invocation."""
    if not _hex(channel_nonce):
        raise V2BError("channel nonce must be a 64-hex string")
    if not isinstance(sources, dict) or set(sources) != set(roles):
        raise V2BError(f"source frames must be exactly {list(roles)}")
    blob = channel_nonce.encode("utf-8") + b"\n"
    for role in roles:                       # frozen order, never dict order
        blob += frame_bytes(role, sources[role])
    blob += b"ENDFRAMES\n"
    if authorize:
        blob += f"GO:{channel_nonce}\n".encode("utf-8")
    return blob


def frame_digests(sources):
    """Role -> SHA256 of the exact bytes placed on the channel."""
    return {role: sha256_bytes(payload) for role, payload in sources.items()}


__all__ = ["FRAME_CHANNEL_CONTRACT", "SUFFIX_FRAME_ROLES",
           "TARGET_FRAME_ROLES", "blank_region", "build_views",
           "channel_payload", "frame_bytes", "frame_digests"]
