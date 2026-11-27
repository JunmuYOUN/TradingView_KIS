"""Per-symbol observed-remove set of drawing versions, including offline edits."""
import hashlib
import json
import re
from dataclasses import dataclass, field


def _digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]


def _identified(drawings):
    """Repair older missing/duplicate IDs identically on either device."""
    used = set()
    reserved = {d["id"] for d in drawings if isinstance(d.get("id"), str) and d["id"]}
    result = []
    for index, drawing in enumerate(drawings):
        key = drawing.get("id")
        if not isinstance(key, str) or not key or key in used:
            key = "legacy-" + _digest([index, drawing])
            while key in used or key in reserved:
                key += "-"
            drawing = {**drawing, "id": key}
        used.add(key)
        result.append(drawing)
    return result


@dataclass
class DrawingSet:
    # clock[writer] covers all operations by this writer on THIS symbol.
    # An absent live dot covered by the clock was deleted or superseded.
    clock: dict = field(default_factory=dict)
    live: dict = field(default_factory=dict)  # (writer, sequence) -> (position, drawing)

    @classmethod
    def legacy(cls, drawings):
        state = cls()
        for index, drawing in enumerate(_identified(drawings)):
            writer = _digest(["legacy", drawing])
            state.clock[writer] = 1
            state.live[writer, 1] = (index, drawing)
        return state

    def packet(self):
        entries = [[writer, seq, position, drawing]
                   for (writer, seq), (position, drawing) in sorted(self.live.items())]
        return {"clock": self.clock, "entries": entries}

    @classmethod
    def parse(cls, packet):
        if not isinstance(packet, dict):
            raise ValueError("그림 동기화 형식 오류")
        clock, entries = packet.get("clock"), packet.get("entries")
        if not isinstance(clock, dict) or not isinstance(entries, list):
            raise ValueError("그림 동기화 목록 오류")
        for writer, seq in clock.items():
            if (not isinstance(writer, str) or not re.fullmatch("[a-f0-9]{32}", writer)
                    or type(seq) is not int or not 1 <= seq < 2**63):
                raise ValueError("그림 변경 버전 오류")
        live = {}
        for entry in entries:
            if not isinstance(entry, list) or len(entry) != 4:
                raise ValueError("그림 변경 기록 오류")
            writer, seq, position, drawing = entry
            if (not isinstance(writer, str) or type(seq) is not int or not 1 <= seq <= clock.get(writer, 0)
                    or type(position) is not int or not 0 <= position < 2**63
                    or not isinstance(drawing, dict) or not isinstance(drawing.get("id"), str)
                    or not drawing["id"] or (writer, seq) in live):
                raise ValueError("그림 변경 항목 오류")
            live[writer, seq] = (position, drawing)
        return cls(dict(clock), live)

    def merge(self, other):
        live = {}
        for source, peer in ((self, other), (other, self)):
            for dot, item in source.live.items():
                writer, seq = dot
                if dot in peer.live:
                    position, drawing = peer.live[dot]
                    if drawing != item[1]:
                        raise ValueError("같은 버전의 그림 내용이 다릅니다.")
                    # Identical legacy drawings may start at different positions.
                    live[dot] = (min(position, item[0]), drawing)
                elif seq > peer.clock.get(writer, 0):
                    live[dot] = item
        clock = dict(self.clock)
        for writer, seq in other.clock.items():
            clock[writer] = max(seq, clock.get(writer, 0))
        return DrawingSet(clock, live)

    def _visible(self):
        """Map concurrent edits to independently editable, deterministic chart IDs."""
        groups = {}
        visible = {}
        for dot, (position, drawing) in sorted(self.live.items()):
            groups.setdefault(drawing["id"], []).append((dot, position, drawing))
        used = set(groups)
        for key, versions in groups.items():
            if len(versions) == 1:
                dot, position, drawing = versions[0]
                visible[key] = (position, drawing, [dot], key)
                continue
            variants = {}
            for dot, position, drawing in versions:
                digest = _digest(drawing)
                if digest in variants:
                    visible[variants[digest]][2].append(dot)
                    continue
                view_id = key
                if variants:
                    view_id = "conflict-" + _digest([key, dot])
                    while view_id in used:
                        view_id += "-"
                    used.add(view_id)
                variants[digest] = view_id
                visible[view_id] = (position, {**drawing, "id": view_id}, [dot], key)
        return visible

    def drawings(self):
        return [item[1] for _, item in sorted(self._visible().items(),
                                              key=lambda pair: (pair[1][0], pair[0]))]

    def replace(self, drawings, writer):
        wanted = {d["id"]: d for d in _identified(drawings)}
        visible = self._visible()
        prior_order = sorted(visible, key=lambda key: (visible[key][0], key))
        normal_order = [key for key in prior_order if key in wanted] + [key for key in wanted if key not in visible]
        reordered = list(wanted) != normal_order
        changed_groups = {origin for key, (_, drawing, _, origin) in visible.items()
                          if wanted.get(key) != drawing or reordered}
        # When one conflicting version is edited, detach its siblings with their
        # visible IDs. Deleting the primary must not rename a surviving copy.
        for _, (_, _, dots, origin) in visible.items():
            if origin in changed_groups:
                for dot in dots:
                    del self.live[dot]
        end = max((item[0] for item in visible.values()), default=-1)
        for index, (key, drawing) in enumerate(wanted.items()):
            prior = visible.get(key)
            if prior and prior[3] not in changed_groups:
                continue
            if reordered:
                position = index
            elif prior:
                position = prior[0]
            else:
                end += 1
                position = end
            seq = self.clock.get(writer, 0) + 1
            self.clock[writer] = seq
            self.live[writer, seq] = (position, drawing)
