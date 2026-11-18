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
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.loader import async_get_loaded_integration

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
        seen: list[str] = []
        for level in self.levels:
            for entity_id in level.entity_ids:
                if entity_id not in seen:
                    seen.append(entity_id)
        return seen

    @property
    def has_fallback(self) -> bool:
        """True when a later integration should be tried if this one fails."""
        return len(self.levels) > 1

    def member_index(self, entity_id: str) -> int | None:
        """Which physical member this entity belongs to, if it is not covering."""
        for index, member in enumerate(self.members):
            if entity_id in member:
                return index
        return None

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
    """
    ent_reg = er.async_get(hass)
    entity_ids = list(entity_ids)
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

    hardware = _hardware_devices(hass)
    grouped: dict[str, LinkedGroup] = {}
    member_entities: set[str] = set()

    for cluster in _clusters(hardware):
        if not any(device.id in step_device_ids for device in cluster):
            continue
        if domain is None:
            continue
        group = _group_from_cluster(
            hass, cluster, ent_reg, domain, step, entity_ids, order
        )
        if group is None:
            continue
        ids = group.entity_ids
        # Place the group at the first of the step's entities that belongs to it.
        anchor = min(
            (entity_id for entity_id in ids if entity_id in step),
            key=entity_ids.index,
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

    def _entity_sort(entity_id: str) -> tuple[int, int, str]:
        if entity_id in entity_order:
            return (0, entity_order.index(entity_id), entity_id)
        return (1, 0, entity_id)

    for domain_ids in by_domain.values():
        domain_ids.sort(key=_entity_sort)

    counts = {name: len(ids) for name, ids in by_domain.items()}
    max_count = max(counts.values())
    covering_domains = {
        name for name, count in counts.items() if count == 1 and max_count > 1
    }
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
    """Integrations with a device that's linked to another integration's device."""
    devices = _hardware_devices(hass)
    domains: set[str] = set()
    entries = hass.config_entries
    for cluster in _clusters(devices):
        cluster_domains = set()
        for device in cluster:
            entry_id = _entry_id(device)
            entry = entries.async_get_entry(entry_id) if entry_id else None
            if entry is not None:
                cluster_domains.add(entry.domain)
        if len(cluster_domains) > 1:
            domains |= cluster_domains
    return sorted(domains, key=lambda d: sort_key(hass, [], d))
