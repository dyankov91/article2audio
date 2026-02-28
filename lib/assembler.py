"""Audio assembly — concatenate WAV chunks into M4B audiobook."""

import os
import struct
import subprocess
import sys


def concat_to_m4b(wav_files: list[str], output_path: str, title: str) -> None:
    """Concatenate WAV chunks into a single M4B audiobook.

    Uses ffmpeg to concat WAVs and encode as AAC in M4B container.
    """
    if not wav_files:
        print("❌ No audio chunks to combine.")
        sys.exit(1)

    tmpdir = os.path.dirname(wav_files[0])

    # Create ffmpeg concat list
    list_path = os.path.join(tmpdir, "filelist.txt")
    with open(list_path, "w") as f:
        for wav in wav_files:
            f.write(f"file '{wav}'\n")

    # Concat WAVs → single WAV
    combined_wav = os.path.join(tmpdir, "combined.wav")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c", "copy", combined_wav,
        ],
        capture_output=True, check=True,
    )

    # Convert to M4B (AAC in M4B container) with metadata
    # movflags +faststart puts the moov atom at the start for better streaming/compatibility
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", combined_wav,
            "-c:a", "aac", "-b:a", "128k",
            "-metadata", f"title={title}",
            "-metadata", "artist=A2Pod",
            "-metadata", "genre=Audiobook",
            "-movflags", "+faststart",
            "-f", "ipod", output_path,
        ],
        capture_output=True, check=True,
    )

    # Set the iTunes media type atom (stik=2 = Audiobook)
    # This is required for Apple Books to recognize it as an audiobook on all devices
    _set_audiobook_media_type(output_path)

    print(f"📁 Saved: {output_path}")


def _find_box(data: bytes, path: list[str], offset: int = 0, end: int | None = None) -> tuple[int, int] | None:
    """Find an MP4 box by navigating a path of box types (e.g. ['moov', 'udta', 'meta'])."""
    if end is None:
        end = len(data)
    box_type = path[0].encode("ascii")
    pos = offset
    while pos < end:
        if pos + 8 > end:
            break
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        if size < 8:
            break
        name = data[pos + 4:pos + 8]
        box_end = pos + size
        if name == box_type:
            if len(path) == 1:
                return (pos, box_end)
            # Container boxes — skip 'meta' full-box header (4 extra bytes)
            header = 8
            if path[0] == "meta":
                header = 12
            return _find_box(data, path[1:], pos + header, box_end)
        pos = box_end
    return None


def _set_audiobook_media_type(filepath: str) -> None:
    """Inject the iTunes 'stik' atom (media type = 2 = Audiobook) into an M4B file.

    Apple Books on iOS uses this atom to identify audiobooks. Without it,
    the file may appear on macOS but won't sync to iPhone/iPad.
    """
    data = bytearray(open(filepath, "rb").read())

    # Navigate: moov → udta → meta → ilst
    # If these boxes don't exist we need to create them
    moov = _find_box(data, ["moov"])
    if moov is None:
        print("⚠️  No moov box found — skipping stik injection.")
        return

    # Build the stik atom: data box + stik wrapper
    #   stik box = [size:4][type:4 'stik'][data box]
    #   data box = [size:4][type:4 'data'][flags:4 = 0x15 (uint8)][locale:4 = 0][value:1 = 2]
    stik_data = struct.pack(">I4sIIB", 17, b"data", 0x00000015, 0, 2)
    stik_box = struct.pack(">I4s", 17 + 8, b"stik") + stik_data

    # Try to find existing ilst to append into
    ilst = _find_box(data, ["moov", "udta", "meta", "ilst"])
    if ilst is not None:
        ilst_start, ilst_end = ilst
        # Check if stik already exists
        if _find_box(data, ["stik"], ilst_start + 8, ilst_end) is not None:
            return  # Already has stik
        # Insert stik at end of ilst, before ilst closing
        insert_pos = ilst_end
        data[insert_pos:insert_pos] = stik_box
        # Update ilst size
        old_size = struct.unpack(">I", data[ilst_start:ilst_start + 4])[0]
        struct.pack_into(">I", data, ilst_start, old_size + len(stik_box))
        # Walk up and update parent box sizes
        for path in [["moov", "udta", "meta"], ["moov", "udta"], ["moov"]]:
            box = _find_box(data, path)
            if box:
                bstart = box[0]
                old = struct.unpack(">I", data[bstart:bstart + 4])[0]
                struct.pack_into(">I", data, bstart, old + len(stik_box))
    else:
        # Build the full chain: udta → meta → ilst → stik
        ilst_box = struct.pack(">I4s", 8 + len(stik_box), b"ilst") + stik_box
        # meta is a full box (version + flags = 4 bytes after header)
        meta_box = struct.pack(">I4sI", 12 + len(ilst_box), b"meta", 0) + ilst_box
        udta_box = struct.pack(">I4s", 8 + len(meta_box), b"udta") + meta_box

        # Check if udta already exists in moov
        udta = _find_box(data, ["moov", "udta"])
        if udta is not None:
            meta = _find_box(data, ["moov", "udta", "meta"])
            if meta is not None:
                # meta exists but no ilst — insert ilst into meta
                meta_start, meta_end = meta
                insert_pos = meta_end
                data[insert_pos:insert_pos] = ilst_box
                old_meta = struct.unpack(">I", data[meta_start:meta_start + 4])[0]
                struct.pack_into(">I", data, meta_start, old_meta + len(ilst_box))
                added = len(ilst_box)
            else:
                # udta exists but no meta — insert meta into udta
                udta_start, udta_end = udta
                insert_pos = udta_end
                data[insert_pos:insert_pos] = meta_box
                old_udta = struct.unpack(">I", data[udta_start:udta_start + 4])[0]
                struct.pack_into(">I", data, udta_start, old_udta + len(meta_box))
                added = len(meta_box)
            # Update moov size
            moov_start = _find_box(data, ["moov"])[0]
            old_moov = struct.unpack(">I", data[moov_start:moov_start + 4])[0]
            struct.pack_into(">I", data, moov_start, old_moov + added)
        else:
            # No udta at all — append udta to end of moov
            moov_start, moov_end = moov
            insert_pos = moov_end
            data[insert_pos:insert_pos] = udta_box
            old_moov = struct.unpack(">I", data[moov_start:moov_start + 4])[0]
            struct.pack_into(">I", data, moov_start, old_moov + len(udta_box))

    with open(filepath, "wb") as f:
        f.write(data)


