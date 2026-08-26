# VNish ASIC Miner — Home Assistant Integration

Intégration Home Assistant complète pour les mineurs ASIC sous firmware **VNish**.

Testée sur **Antminer S19k Pro** avec **VNish 1.3.5**, et compatible avec la gamme
**S19 / S21 / T21 / L7** sous VNish (l'API REST exposée par VNish est commune à
l'ensemble de ces modèles).

## Fonctionnalités

- **Capteurs** : hashrate instantané / moyen / nominal, puissance consommée,
  températures max puces/PCB, vitesse max ventilateurs, efficacité (J/TH),
  statut du mineur.
- **Sélecteur de profil (`select`)** : bascule dynamique entre tous les
  presets d'overclock/autotune disponibles sur le mineur (ex. `2050`, `2180`,
  `2310`, `2470`, `2600`, `2990`, `3120`) — idéal pour les automatisations
  Heures Creuses / Heures Pleines.
- **Interrupteur de minage (`switch`)** : pause / reprise du minage.
- **Boutons d'action** : redémarrage logiciel du minage et reboot matériel
  de l'ASIC.
- **Config Flow** natif avec validation de la connexion et de la clé API,
  et **Options Flow** pour ajuster l'intervalle de rafraîchissement ou la
  clé API après coup.

## Installation

### Via HACS (recommandé)

1. Dans HACS, ouvrez **Intégrations** → menu **⋮** → **Dépôts personnalisés**.
2. Ajoutez `https://github.com/Clemiax/ha-vnish-miner` en tant que type
   *Intégration*.
3. Recherchez **VNish ASIC Miner** dans HACS et installez-le.
4. Redémarrez Home Assistant.

### Installation manuelle

1. Copiez le dossier `custom_components/vnish_miner` dans le dossier
   `custom_components` de votre configuration Home Assistant.
2. Redémarrez Home Assistant.

## Configuration

Dans Home Assistant : **Paramètres → Appareils et services → Ajouter une
intégration → VNish ASIC Miner**.

| Champ | Description | Défaut |
|---|---|---|
| Host | Adresse IP ou nom d'hôte du mineur | — |
| Clé API | Clé API VNish (en-tête `X-API-Key`) | — |
| Port | Port de l'API REST du mineur | `80` |
| Intervalle de rafraîchissement | Fréquence de polling en secondes | `15` |

La clé API se génère depuis l'interface web VNish du mineur
(*Settings → API access*).

## Entités créées

| Entité | Type | Exemple d'`entity_id` |
|---|---|---|
| Hashrate instantané | sensor | `sensor.antminer_s19kpro_instant_hashrate` |
| Hashrate moyen | sensor | `sensor.antminer_s19kpro_average_hashrate` |
| Hashrate nominal | sensor | `sensor.antminer_s19kpro_nominal_hashrate` |
| Puissance consommée | sensor | `sensor.antminer_s19kpro_power_consumption` |
| Température max puces | sensor | `sensor.antminer_s19kpro_max_chip_temperature` |
| Température max PCB | sensor | `sensor.antminer_s19kpro_max_pcb_temperature` |
| Vitesse max ventilateurs | sensor | `sensor.antminer_s19kpro_max_fan_speed` |
| Efficacité | sensor | `sensor.antminer_s19kpro_efficiency` |
| Statut du mineur | sensor | `sensor.antminer_s19kpro_miner_status` |
| Profil overclock | select | `select.antminer_s19kpro_overclock_preset` |
| Minage | switch | `switch.antminer_s19kpro_mining` |
| Redémarrer le minage | button | `button.antminer_s19kpro_restart_mining` |
| Redémarrer le matériel ASIC | button | `button.antminer_s19kpro_reboot_asic_hardware` |

## Exemples d'automatisations

### Basculement Heures Creuses / Heures Pleines

Profil de puissance élevé pendant les heures creuses, profil réduit le reste
du temps, afin d'optimiser le coût de l'électricité.

```yaml
automation:
  - alias: "VNish - Profil Heures Creuses (puissance haute)"
    description: "Bascule le mineur sur le preset haute performance en heures creuses"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: select.select_option
        target:
          entity_id: select.antminer_s19kpro_overclock_preset
        data:
          option: "3120"

  - alias: "VNish - Profil Heures Pleines (puissance réduite)"
    description: "Bascule le mineur sur un preset réduit en heures pleines"
    trigger:
      - platform: time
        at: "06:00:00"
    action:
      - service: select.select_option
        target:
          entity_id: select.antminer_s19kpro_overclock_preset
        data:
          option: "2050"
```

### Sécurité surchauffe

Met le minage en pause automatiquement si la température des puces dépasse
un seuil critique, et le reprend une fois la température redescendue.

```yaml
automation:
  - alias: "VNish - Sécurité surchauffe (pause)"
    description: "Met le mineur en pause si la température des puces est critique"
    trigger:
      - platform: numeric_state
        entity_id: sensor.antminer_s19kpro_max_chip_temperature
        above: 90
        for:
          minutes: 1
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.antminer_s19kpro_mining
      - service: notify.mobile_app
        data:
          title: "⚠️ Surchauffe mineur ASIC"
          message: "Minage mis en pause : température puces > 90°C."

  - alias: "VNish - Reprise après refroidissement"
    description: "Reprend le minage une fois la température revenue à la normale"
    trigger:
      - platform: numeric_state
        entity_id: sensor.antminer_s19kpro_max_chip_temperature
        below: 80
        for:
          minutes: 5
    condition:
      - condition: state
        entity_id: switch.antminer_s19kpro_mining
        state: "off"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.antminer_s19kpro_mining
```

### Redémarrage automatique en cas de blocage

```yaml
automation:
  - alias: "VNish - Redémarrage si mineur bloqué"
    description: "Relance le processus de minage si le statut reste anormal"
    trigger:
      - platform: state
        entity_id: sensor.antminer_s19kpro_miner_status
        to: "error"
        for:
          minutes: 3
    action:
      - service: button.press
        target:
          entity_id: button.antminer_s19kpro_restart_mining
```

## Développement

### Structure

```
custom_components/vnish_miner/
├── __init__.py          # Mise en place de l'entrée de config et des plateformes
├── button.py             # Boutons (restart mining / reboot hardware)
├── config_flow.py        # Config flow + options flow
├── const.py               # Constantes du domaine
├── coordinator.py        # DataUpdateCoordinator (summary/info/status/settings/presets)
├── entity.py              # Entité de base partagée
├── manifest.json
├── select.py               # Sélecteur de preset d'overclock
├── sensor.py                # Capteurs de métriques
├── strings.json / translations/
├── switch.py                # Interrupteur pause/reprise du minage
└── vnish_client.py          # Client REST async (aiohttp)
```

### Tests

Un script de tests unitaires est fourni dans `tests/test_vnish.py`. Il mocke
la session `aiohttp` et valide :

- le parsing des réponses JSON pour chaque endpoint (`summary`, `info`,
  `status`, `settings`, `autotune/presets`) ;
- le payload envoyé lors du changement de preset ;
- les appels aux endpoints d'action (`pause`, `resume`, `restart`, `reboot`) ;
- la gestion des erreurs d'authentification (401/403) et de connexion.

```bash
pip install aiohttp pytest
python3 -m pytest tests/test_vnish.py -v
```

## Avertissement

Cette intégration n'est pas affiliée à VNish ni à Bitmain. Utilisez-la à vos
propres risques ; le pilotage à distance de l'overclock ou du reboot d'un
mineur ASIC peut affecter sa stabilité matérielle.
