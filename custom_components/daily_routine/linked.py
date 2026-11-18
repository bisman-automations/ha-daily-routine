"""One device in more than one integration (1.21.0, integration levels in 1.32.0).

A SwitchBot curtain can be in Home Assistant through Matter, Bluetooth and the
cloud: three entities, three devices, which Home Assistant shows as linked
because they share an identifier or connection (a MAC address, for example).
A step treats those as one device and tries one integration at a time, in an
order: the routine's own, else the one in Settings, else local integrations
before cloud ones. The next integration is used only if this one failed or
the device didn't change.

A Matter device can also be a pair: one entity for two curtains that SwitchBot
exposes separately (left and right, each over Bluetooth and the cloud). Those
devices are still one cluster — they share connections, and a pair device that
is in two Device Linker links carries both sides' addresses, so the sides join
up. The pair entity is used as one call only when the step is for both
curtains (or names the pair itself). A step for only the left or right leaves
the pair alone and uses that side's integrations in the same order.

When the step lists only a Bluetooth or cloud entity, a higher-priority way
that covers the same curtains (the Matter entity, for example) is still
brought in as the first level, as long as the pair rule above allows it.

Entities are only grouped when it's safe: a two-channel switch (two of the
step's entities on one device) is never grouped, so both channels still get
the command. Service devices (a routine, a Device Linker hub) are not part of
a cluster.

Speed (1.33.0): the clusters are worked out once, with an index of which
cluster each device is in, and each step's groups are kept too
(``registry_cache``); both are dropped when a device, entity or area changes
or an integration loads. A step only looks at the clusters of its own
devices, not at every cluster in the house.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.loader import async_get_loaded_integration

from .registry_cache import cached

# How close an integration is to the device, best first, when no order is set.
IOT_RANK = {
    "local_push": 0,
    "local_polling": 1,
    "assumed_state": 2,
    "cloud_push": 3,
    "cloud_polling": 4,
}


def integration_rank(hass: HomeAssistant, domain: str) -> int:
    try:
        iot_class = async_get_loaded_integration(hass, domain).iot_class
    except Exception:  # noqa: BLE001 - not loaded, or no manifest value
        return len(IOT_RANK)
    return IOT_RANK.get(iot_class or "", len(IOT_RANK))


def integration_name(hass: HomeAssistant, domain: str) -> str:
    try:
        return async_get_loaded_integration(hass, domain).name
    except Exception:  # noqa: BLE001
        return domain


def sort_key(
    hass: HomeAssistant, order: list[str], domain: str
) -> tuple[int, int, str]:
    """Chosen order first; then local before cloud; then by name."""
    if domain in order:
        return (0, order.index(domain), domain)
    return (1, integration_rank(hass, domain), domain)


@dataclass(frozen=True)
class IntegrationLevel:
    """One integration's way or ways to reach the devices in a group.

    ``entity_ids`` is one entity for a single curtain or a covering pair, or
    every member at this integration (left and right over Bluetooth, for
    example). ``covering`` is true when this entity stands for more than one
    member (a Matter pair for both curtains).
    """

    domain: str
    entity_ids: tuple[str, ...]
    covering: bool = False


@dataclass(frozen=True)
class LinkedGroup:
    """One logical device (or a pair) and the integrations that can reach it.

    ``levels`` are in the order they should be tried, best first. ``members``
    lists each physical member's alternative entity ids (the left curtain's
    Bluetooth and cloud entities, then the right's). A covering pair entity
    is in ``levels`` only, not in ``members``.
    """

    levels: tuple[IntegrationLevel, ...]
    members: tuple[tuple[str, ...], ...] = ()

    @property
    def entity_ids(self) -> list[str]:
        """Every entity in the group, levels first, without duplicates."""
        # A dict keeps the first place of each entity, without a list search.
        return list(
            dict.fromkeys(
                entity_id for level in self.levels for entity_id in level.entity_ids
            )
        )

    @property
    def has_fallback(self) -> bool:
        """True when a later integration should be tried if this one fails."""
        return len(self.levels) > 1

    @cached_property
    def _member_of(self) -> dict[str, int]:
        """Entity id -> its member, built once (the group never changes).

        The first member wins, as the search it replaces did.
        """
        found: dict[str, int] = {}
        for index, member in enumerate(self.members):
            for entity_id in member:
                found.setdefault(entity_id, index)
        return found

    def member_index(self, entity_id: str) -> int | None:
        """Which physical member this entity belongs to, if it is not covering."""
        return self._member_of.get(entity_id)

    def level_entities_for(
        self, level: IntegrationLevel, remaining: set[int]
    ) -> list[str]:
        """Entities of this level that still need a command."""
        if level.covering:
            return list(level.entity_ids) if remaining else []
        if not self.members:
            return list(level.entity_ids)
        return [e for e in level.entity_ids if self.member_index(e) in remaining]


def _device_keys(device: Any) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for identifier in device.identifiers:
        keys.add(("id", *map(str, identifier)))
    for connection in device.connections:
        keys.add(("conn", *map(str, connection)))
    return keys


def _clusters(devices: list[Any]) -> list[list[Any]]:
    """Devices that share any identifier or connection, joined up.

    A device in two links (a Matter pair carrying both curtains' MAC
    addresses) joins those two sides into one cluster.
    """
    parent = {device.id: device.id for device in devices}

    def find(device_id: str) -> str:
        while parent[device_id] != device_id:
            parent[device_id] = parent[parent[device_id]]
            device_id = parent[device_id]
        return device_id

    seen: dict[tuple[str, ...], str] = {}
    for device in devices:
        for key in _device_keys(device):
            if key in seen:
                parent[find(device.id)] = find(seen[key])
            else:
                seen[key] = device.id
    groups: dict[str, list[Any]] = {}
    for device in devices:
        groups.setdefault(find(device.id), []).append(device)
    return list(groups.values())


def _hardware_devices(hass: HomeAssistant) -> list[Any]:
    """Registry devices that can belong to a linked cluster (not services)."""
    items = dr.async_get(hass).devices
    # A mapping of id to device before 2026.9, the devices themselves after.
    every = list(items.values()) if isinstance(items, Mapping) else list(items)
    return [
        device
        for device in every
        if device.entry_type is None
        and not getattr(device, "is_composite_device", False)
    ]


def _topology(hass: HomeAssistant) -> tuple[list[list[Any]], dict[str, int]]:
    """Every linked cluster, and which cluster each device is in (kept)."""

    def build() -> tuple[list[list[Any]], dict[str, int]]:
        clusters = _clusters(_hardware_devices(hass))
        index = {
            device.id: number
            for number, cluster in enumerate(clusters)
            for device in cluster
        }
        return clusters, index

    return cached(hass, ("linked", "topology"), build)


def _entry_id(device: Any) -> str | None:
    """The device's config entry (one per device since Home Assistant 2026.8)."""
    return getattr(device, "config_entry_id", None)


def _domain_entities(
    ent_reg: er.EntityRegistry, device_id: str, domain: str
) -> list[er.RegistryEntry]:
    return [
        entry
        for entry in er.async_entries_for_device(ent_reg, device_id)
        if entry.disabled_by is None and entry.entity_id.split(".", 1)[0] == domain
    ]


def _pick_entities(
    entries: list[er.RegistryEntry], step: set[str]
) -> list[er.RegistryEntry]:
    """The step's entities on this device, or its only one of that domain.

    A device with two covers (the curtain and its tilt, for example) only
    contributes the one the step already named. A device with a single cover
    that the step didn't name (the Matter pair) can still be brought in.
    """
    in_step = [entry for entry in entries if entry.entity_id in step]
    if in_step:
        return in_step
    if len(entries) == 1:
        return entries
    return []


def _subclusters(devices: list[Any], skip_ids: set[str]) -> list[list[Any]]:
    """Re-cluster ``devices`` after taking covering pair devices out.

    Left and right then only stay together when they still share an
    identifier or connection of their own (Device Linker copied one MAC onto
    every member). Otherwise each side is its own member.
    """
    remaining = [device for device in devices if device.id not in skip_ids]
    if not remaining:
        return []
    return _clusters(remaining)


def linked_groups(
    hass: HomeAssistant, entity_ids: Iterable[str], order: list[str]
) -> list[LinkedGroup]:
    """The step's entities as devices, each with its integrations, best first.

    Unlinked entities come back as a group of one, in the order given. A
    higher-priority entity that covers the same targets is included even when
    the step didn't name it, except a pair entity is only included when the
    step is for everything it covers (or names the pair itself).

    Kept per (entities, order) until the registries change; the groups are
    frozen, so the same ones can be handed out again (in a new list).
    """
    entity_ids = list(entity_ids)
    key = ("linked", "groups", tuple(entity_ids), tuple(order))
    return list(cached(hass, key, lambda: _linked_groups(hass, entity_ids, order)))


def _linked_groups(
    hass: HomeAssistant, entity_ids: list[str], order: list[str]
) -> list[LinkedGroup]:
    """``linked_groups`` itself, worked out from the registries."""
    ent_reg = er.async_get(hass)
    step = set(entity_ids)
    if not entity_ids:
        return []

    domains = {entity_id.split(".", 1)[0] for entity_id in entity_ids}
    domain = next(iter(domains)) if len(domains) == 1 else None

    step_device_ids: set[str] = set()
    for entity_id in entity_ids:
        entry = ent_reg.async_get(entity_id)
        if entry is not None and entry.device_id is not None:
            step_device_ids.add(entry.device_id)

    grouped: dict[str, LinkedGroup] = {}
    member_entities: set[str] = set()
    # Where each of the step's entities is in the step (its first place).
    position: dict[str, int] = {}
    for number, entity_id in enumerate(entity_ids):
        position.setdefault(entity_id, number)

    clusters, device_cluster = _topology(hass) if domain is not None else ([], {})
    # Only the clusters the step's devices are in, in the same order as when
    # every cluster was looked at.
    wanted = sorted({device_cluster[d] for d in step_device_ids if d in device_cluster})
    for number in wanted:
        cluster = clusters[number]
        group = _group_from_cluster(
            hass, cluster, ent_reg, domain or "", step, entity_ids, order
        )
        if group is None:
            continue
        ids = group.entity_ids
        # Place the group at the first of the step's entities that belongs to it.
        anchor = min(
            (entity_id for entity_id in ids if entity_id in step),
            key=position.__getitem__,
            default=ids[0],
        )
        grouped[anchor] = group
        member_entities.update(ids)

    result: list[LinkedGroup] = []
    seen_groups: set[int] = set()
    for entity_id in entity_ids:
        if entity_id in grouped:
            group = grouped[entity_id]
            marker = id(group)
            if marker not in seen_groups:
                seen_groups.add(marker)
                result.append(group)
        elif entity_id not in member_entities:
            result.append(_singleton(entity_id))
    return result


def _singleton(entity_id: str) -> LinkedGroup:
    domain = entity_id.split(".", 1)[0]
    return LinkedGroup(
        levels=(IntegrationLevel(domain=domain, entity_ids=(entity_id,)),),
        members=((entity_id,),),
    )


def _group_from_cluster(
    hass: HomeAssistant,
    cluster: list[Any],
    ent_reg: er.EntityRegistry,
    domain: str,
    step: set[str],
    entity_order: list[str],
    order: list[str],
) -> LinkedGroup | None:
    """Build one group from a linked cluster, or None if it must stay ungrouped.

    A cluster of one device is left alone. A device with two of the step's
    entities (a two-channel switch) is never grouped, so both channels still
    get the command. Several devices from the same config entry (left and
    right over the same Bluetooth integration) stay in the group: they become
    one level, not alternatives for each other.
    """
    cluster_ids = {device.id for device in cluster}
    step_on_device: dict[str, list[str]] = {}
    for entity_id in entity_order:
        entry = ent_reg.async_get(entity_id)
        if (
            entry is None
            or entry.device_id is None
            or entry.device_id not in cluster_ids
        ):
            continue
        step_on_device.setdefault(entry.device_id, []).append(entity_id)
    if any(len(ids) != 1 for ids in step_on_device.values()):
        return None
    if len(cluster) < 2:
        return None

    # The step's entities, plus a single same-domain entity on a linked
    # device the step didn't name (the Matter pair, for example). A device
    # with two covers (the curtain and its tilt) only contributes the one
    # the step already named.
    chosen: list[er.RegistryEntry] = []
    for device in cluster:
        chosen.extend(
            _pick_entities(_domain_entities(ent_reg, device.id, domain), step)
        )
    if len(chosen) < 2:
        return None

    by_domain: dict[str, list[str]] = {}
    platform: dict[str, str] = {}
    entity_device: dict[str, str] = {}
    for entry in chosen:
        if entry.device_id is None:
            continue
        platform[entry.entity_id] = entry.platform
        entity_device[entry.entity_id] = entry.device_id
        by_domain.setdefault(entry.platform, []).append(entry.entity_id)
    if not by_domain:
        return None

    position: dict[str, int] = {}
    for number, entity_id in enumerate(entity_order):
        position.setdefault(entity_id, number)

    def _entity_sort(entity_id: str) -> tuple[int, int, str]:
        if entity_id in position:
            return (0, position[entity_id], entity_id)
        return (1, 0, entity_id)

    for domain_ids in by_domain.values():
        domain_ids.sort(key=_entity_sort)

    counts = {name: len(ids) for name, ids in by_domain.items()}
    max_count = max(counts.values())
    covering_domains = {
        name for name, count in counts.items() if count == 1 and max_count > 1
    }
    covering_domains = _real_pairs(cluster, covering_domains, by_domain, entity_device)
    covering_device_ids = {
        entity_device[ids[0]]
        for name, ids in by_domain.items()
        if name in covering_domains
    }

    # Physical members (the left curtain, the right curtain). Covering pair
    # devices are taken out so a pair that carries both sides' addresses
    # does not glue those sides into one member.
    members, entity_member = _members_of_cluster(
        cluster,
        covering_device_ids,
        covering_domains,
        entity_device,
        platform,
        by_domain,
        _entity_sort,
    )

    in_step = {entity_id for entity_id in entity_device if entity_id in step}
    pair_named = any(
        entity_id in step
        for name, ids in by_domain.items()
        if name in covering_domains
        for entity_id in ids
    )
    targeted = {
        entity_member[entity_id] for entity_id in in_step if entity_id in entity_member
    }
    if covering_domains and members and not targeted and not pair_named:
        # Unsplittable cluster: members were taken from one integration, and
        # the step may have named the other integration's matching entities
        # (both cloud covers). That still means both sides.
        for name, ids in by_domain.items():
            if name in covering_domains:
                continue
            if len(ids) == max_count and all(entity_id in step for entity_id in ids):
                targeted = set(range(len(members)))
                break

    if not covering_domains:
        # 1:1 alternatives for one device: every linked integration may be used.
        full_coverage = True
        if not members:
            all_entities = tuple(
                entity_id
                for name, ids in sorted(
                    by_domain.items(), key=lambda item: sort_key(hass, order, item[0])
                )
                for entity_id in ids
            )
            members = (all_entities,)
        targeted = set(range(len(members)))
    else:
        full_coverage = pair_named or (
            bool(members) and targeted == set(range(len(members)))
        )

    use_covering = bool(covering_domains) and full_coverage

    levels: list[IntegrationLevel] = []
    for name in sorted(by_domain, key=lambda item: sort_key(hass, order, item)):
        ids = by_domain[name]
        if name in covering_domains:
            if use_covering:
                levels.append(
                    IntegrationLevel(domain=name, entity_ids=tuple(ids), covering=True)
                )
            continue
        if full_coverage:
            chosen_ids = list(ids)
        else:
            chosen_ids = [
                entity_id
                for entity_id in ids
                if entity_id in step or entity_member.get(entity_id) in targeted
            ]
        if chosen_ids:
            levels.append(
                IntegrationLevel(
                    domain=name, entity_ids=tuple(chosen_ids), covering=False
                )
            )

    if not levels:
        return None
    # Only the original entity, no extra ways: leave it ungrouped.
    if (
        len(levels) == 1
        and len(levels[0].entity_ids) == 1
        and levels[0].entity_ids[0] in step
        and len(members) <= 1
    ):
        return None

    kept = {entity_id for level in levels for entity_id in level.entity_ids}
    slim_members = [
        tuple(entity_id for entity_id in member if entity_id in kept)
        for member in members
    ]
    slim_members = [member for member in slim_members if member]
    if not slim_members:
        slim_members = [
            tuple(level.entity_ids) for level in levels if not level.covering
        ] or [tuple(levels[0].entity_ids)]
    return LinkedGroup(levels=tuple(levels), members=tuple(slim_members))


def _real_pairs(
    cluster: list[Any],
    candidates: set[str],
    by_domain: dict[str, list[str]],
    entity_device: dict[str, str],
) -> set[str]:
    """Keep only the integrations whose single entity really covers both sides.

    One entity of an integration among two of another looks like a pair (the
    Matter pair beside left and right SwitchBot). It is only one when its
    device links to more than one side; a right curtain that is the only one
    in SwitchBot Cloud (the left was never added) is just the right curtain.
    When the sides can't be told apart (one shared address on every device),
    the candidates stay as they are.
    """
    if not candidates:
        return candidates
    candidate_devices = {
        name: entity_device[by_domain[name][0]]
        for name in candidates
        if by_domain[name][0] in entity_device
    }
    with_entities = set(entity_device.values())
    sides = [
        side
        for side in _subclusters(cluster, set(candidate_devices.values()))
        if any(device.id in with_entities for device in side)
    ]
    if len(sides) < 2:
        return candidates
    side_keys = [
        set().union(*(_device_keys(device) for device in side)) for side in sides
    ]
    by_id = {device.id: device for device in cluster}
    kept: set[str] = set()
    for name, device_id in candidate_devices.items():
        keys = _device_keys(by_id[device_id])
        if sum(1 for each in side_keys if keys & each) >= 2:
            kept.add(name)
    return kept


def _members_of_cluster(
    cluster: list[Any],
    covering_device_ids: set[str],
    covering_domains: set[str],
    entity_device: dict[str, str],
    platform: dict[str, str],
    by_domain: dict[str, list[str]],
    entity_sort: Any,
) -> tuple[tuple[tuple[str, ...], ...], dict[str, int]]:
    """Each physical member and a map of non-covering entity to member index."""
    sides = _subclusters(cluster, covering_device_ids)
    members: list[tuple[str, ...]] = []
    entity_member: dict[str, int] = {}
    for side in sides:
        side_ids = {device.id for device in side}
        side_entities = [
            entity_id
            for entity_id, device_id in entity_device.items()
            if device_id in side_ids and platform.get(entity_id) not in covering_domains
        ]
        side_entities.sort(key=entity_sort)
        if not side_entities:
            continue
        index = len(members)
        members.append(tuple(side_entities))
        for entity_id in side_entities:
            entity_member[entity_id] = index

    # One shared address on every member leaves a single blob. Split by the
    # integration with the most entities (left BLE and right BLE).
    max_count = max((len(ids) for ids in by_domain.values()), default=0)
    if covering_domains and len(members) <= 1 and max_count > 1:
        members = []
        entity_member = {}
        split_domain = max(
            (name for name in by_domain if name not in covering_domains),
            key=lambda name: len(by_domain[name]),
            default=None,
        )
        if split_domain is not None:
            for entity_id in by_domain[split_domain]:
                index = len(members)
                members.append((entity_id,))
                entity_member[entity_id] = index
    return tuple(members), entity_member


def linked_integrations(hass: HomeAssistant) -> list[str]:
    """Integrations with a device that's linked to another integration's device.

    Kept until the registries change, like the clusters it comes from.
    """
    return list(
        cached(hass, ("linked", "integrations"), lambda: _linked_integrations(hass))
    )


def _linked_integrations(hass: HomeAssistant) -> list[str]:
    domains: set[str] = set()
    entries = hass.config_entries
    clusters, _index = _topology(hass)
    for cluster in clusters:
        cluster_domains = set()
        for device in cluster:
            entry_id = _entry_id(device)
            entry = entries.async_get_entry(entry_id) if entry_id else None
            if entry is not None:
                cluster_domains.add(entry.domain)
        if len(cluster_domains) > 1:
            domains |= cluster_domains
    return sorted(domains, key=lambda d: sort_key(hass, [], d))


# ----- 1.34.0: the routine's linked device first -----
#
# A routine can be linked to a device ("Linked device" under More options),
# usually the Matter one. When a step reaches that device, or the devices it
# is linked to, the linked device is tried first, on its own, and the other
# integrations follow in the integration order only if it doesn't do it. Its
# linked siblings are never sent the command at the same time.


def lead_entities(hass: HomeAssistant, device_id: str | None, domain: str) -> list[str]:
    """The linked device's entities that this action is for (its covers, for example)."""
    if not device_id or dr.async_get(hass).async_get(device_id) is None:
        return []
    return [
        entry.entity_id
        for entry in _domain_entities(er.async_get(hass), device_id, domain)
    ]


def lead_first(
    groups: list[LinkedGroup], lead: list[str]
) -> tuple[list[LinkedGroup], int | None]:
    """Put the linked device's level first in the group it is in.

    Returns the groups (that one with its levels reordered, the others as they
    were) and which group it is, or None when no group with more than one way
    has it (then nothing changes here; see ``area_fallbacks``). The other levels keep their order (the integration order).
    """
    wanted = set(lead)
    for index, group in enumerate(groups):
        if not group.has_fallback:
            continue  # a device on its own: nothing to put it ahead of
        first = [lvl for lvl in group.levels if wanted & set(lvl.entity_ids)]
        if not first:
            continue
        rest = [lvl for lvl in group.levels if lvl not in first]
        led = LinkedGroup(levels=(*first, *rest), members=group.members)
        return [*groups[:index], led, *groups[index + 1 :]], index
    return groups, None


def _device_class(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is not None and state.attributes.get("device_class"):
        return str(state.attributes["device_class"])
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    return entry.device_class or entry.original_device_class


def area_fallbacks(
    hass: HomeAssistant,
    lead: list[str],
    entities: list[str],
    area_entities: list[str],
    order: list[str],
) -> list[tuple[str, ...]]:
    """Without Device Linker: the step's other devices in the linked device's
    room, of the same kind, as fallbacks, one level per integration in order.

    Only used when the step reaches the linked device itself. "Same kind" is
    the same domain (the step's entities already are) and, when the linked
    device says, the same device class: curtains fall back to curtains, not
    to the room's blind or garage door.
    """
    if not lead or not set(lead) & set(entities):
        return []
    in_room = set(area_entities)
    kind = _device_class(hass, lead[0])
    ent_reg = er.async_get(hass)
    by_platform: dict[str, list[str]] = {}
    for entity_id in entities:
        if entity_id in lead or entity_id not in in_room:
            continue
        if kind and _device_class(hass, entity_id) not in (kind, None):
            continue
        entry = ent_reg.async_get(entity_id)
        platform = entry.platform if entry else entity_id.split(".", 1)[0]
        by_platform.setdefault(platform, []).append(entity_id)
    return [
        tuple(by_platform[name])
        for name in sorted(by_platform, key=lambda name: sort_key(hass, order, name))
    ]
