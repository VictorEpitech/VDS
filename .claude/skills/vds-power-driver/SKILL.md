---
name: vds-power-driver
description: Créer ou corriger un pilote d'alimentation VDS (module kind power-driver - ADB Android, API WebSocket Samsung, Wake-on-LAN, HDMI-CEC, écran noir, et plus tard cloud, infrarouge, prise connectée). Utiliser pour tout ce qui allume, éteint ou lit l'état d'une TV.
---

# Pilote d'alimentation VDS

Suis d'abord le skill `vds-module` (format commun). Ce skill ajoute les règles propres aux pilotes.

## Interface (packages/sdk-py)

```python
class PowerDriver(Module):
    async def probe(
        self, screen: ScreenInfo
    ) -> ProbeResult: ...  # ce pilote marche-t-il sur cet écran ?
    async def on(self, screen: ScreenInfo) -> None: ...
    async def off(self, screen: ScreenInfo) -> None: ...
    async def state(self, screen: ScreenInfo) -> PowerState: ...  # ON | OFF | UNKNOWN

    supports: set[Literal["on", "off", "state"]]
```

- Le module tourne sur l'agent de site (`"targets": ["site-agent"]`), permission `lan.access`.
- Chaque appel a un délai borné (config `timeout_ms`), lève `PowerDriverError(code=...)` avec un code stable.
- `probe` ne doit rien allumer ni éteindre.

## Cascade (côté agent, pas dans le pilote)

Le pilote fait une seule chose. La cascade (ordre, vérification par `state`, retry ×1, bascule, événements `power.changed` / `power.failed`) est gérée par l'agent. Ne la réimplémente pas dans un pilote.

## Cibles V1 et points d'attention

| Pilote | Notes |
| --- | --- |
| `power.adb` (TCL Google TV) | ADB réseau à activer une fois sur la TV ; `KEYCODE_SLEEP` / `KEYCODE_WAKEUP` ; état via `dumpsys power` |
| `power.samsung-ws` | API WebSocket locale (port 8002, TLS), autorisation à accepter une fois sur la TV, jeton à stocker chiffré ; l'allumage passe souvent par WoL |
| `power.wol` | Paquet magique sur le broadcast du VLAN ; option « allumage par le réseau » à activer sur la TV |
| `power.cec` | Via le player Pi (libcec) : `on`, `standby`, puis Active Source |
| `power.blank` | Écran noir côté player, toujours disponible, dernier recours |

Vérifie les détails de protocole sur des sources récentes avant d'implémenter : ils changent avec les firmwares.

## Tests

- Faux appareil (simulateur) pour chaque protocole : nominal, refus, délai dépassé, état incohérent.
- `docs/drivers/<id>.md` : procédure de test manuelle sur une vraie TV et réglages à faire sur l'appareil.
