# Validation réelle — 13 septembre 2026

Les treize rapports requis passent. Les données détaillées sont dans
[validation-results.json](validation-results.json). Les captures PNG, callbacks
JSON et journaux complets sont générés localement dans `artifacts/` (exclu de Git).
Le dépôt inclut les résultats résumés et une sélection de captures vérifiées.

## Mesures sur cette machine

SteamOS **3.8.26**, KWin **6.4.3**, Python **3.13.5**. Les conteneurs utilisent
Hyprland **0.56.2**, Sway **1.12**, KWin **6.7.5**, Python **3.14.7**,
GStreamer **1.28.7** et PipeWire **1.6.8**.

Toutes les valeurs sont en millisecondes, transport MCP local inclus.

| Moteur | Capture médiane | Capture p95 | Action → image vérifiée, médiane |
|---|---:|---:|---:|
| KWin privé, SteamOS natif | 2.13 | 2.47 | 53.83 |
| Portail KDE, KWin privé | 2.15 | 2.34 | 53.07 |
| Hyprland privé, conteneur | 32.14 | 34.03 | 55.77 |
| Sway/Pixman, conteneur | 16.15 | 32.1 | 50.44 |
| Xvfb, conteneur | 16.46 | 17.0 | 50.2 |

Une capture mesure 30 appels à chaud, à 1280×800, JPEG qualité 85. Pour PipeWire,
c'est la lecture/encodage de la dernière image disponible. Pour wlroots/X11,
une nouvelle capture est demandée à chaque appel. Ce ne sont donc pas des coûts
de capture identiques. La dernière colonne mesure cinq actions AT-SPI : callback
confirmé par l'application, puis image PNG différente et postérieure à l'action.
Elle inclut le délai de rendu de 40 ms. Les images répétées par le keepalive
PipeWire ne sont pas considérées comme un rafraîchissement.

Les mesures excluent l'inférence du LLM, le réseau et les délais d'une application
distante. Les petits échantillons caractérisent cette exécution, pas tous les
matériels. **Aucune comparaison macOS/Windows n'a été effectuée.**

## Ce qui a été vérifié

- MCP : initialisation, découverte des 19 outils, blocs image natifs et JSON structuré.
- Vraie application GTK : fenêtres/focus, arbre AT-SPI et actions sémantiques.
- Saisie exacte de français, japonais et emoji ; remplacement par Ctrl+A.
- Clic, double-clic, glissement et défilement confirmés par callbacks natifs.
- Coordonnées dans une image réduite de moitié ; références périmées et positions
  hors écran refusées ; actions groupées et annulation pendant un glissement.
- Cinq modifications successives du GUI donnent chacune une image actualisée.
- Qt natif : dialogue kdialog, Unicode, bouton OK AT-SPI, texte retourné exact.
- XWayland : xterm avec liaison CLIPBOARD explicite, texte Unicode reçu sur stdin.
- Deux écrans Sway : second écran à x=1280, échelle 125 %, capture physique 1600×1200
  réduite à 800 pixels ; le clic atteint bien l'application sur ce second écran.
- Arrêt normal : processus possédés terminés et récupérés. EOF et SIGTERM quittent
  proprement. SIGKILL déclenche le gardien ; aucun processus privé ne reste actif.
- CLI réelle : socket 0600, fichier JSON Unicode littéral, export JPEG, fermeture.
- Copie autonome du skill installée dans un autre répertoire, puis suite MCP complète.
- Vingt ouvertures/fermetures d'applications avec lectures AT-SPI pendant leurs
  changements d'arbre ; toutes les opérations AT-SPI et ses événements partagent
  le même thread, évitant les courses dans le cache natif.
- Code embarqué dans l'image Podman : suites complètes Sway et Xvfb sans GPU,
  sans réseau, sans root et avec système de fichiers racine en lecture seule.
- Six tests de contrats : types, nombres non finis, cycle de session, permissions
  du socket, raccourcis clavier et erreurs de trame MCP. Compilation Python et
  validation du format SKILL.md réussies.

Le test du portail utilise un **vrai portail KDE dans un bureau créé par le test**.
Avant d'accepter Share, il vérifie le chemin du socket Wayland, un bus différent
du bus physique et le PID exact du dbus-daemon enfant. Le dialogue identifie
« Linux Computer Use » ; la restauration future est décochée. Cette automatisation
est uniquement dans le test opt-in, jamais dans le runtime de production.

## Reproduction depuis le dépôt

```sh
python3 tests/contracts.py
python3 tests/e2e.py --compositor kwin --output artifacts/e2e-kwin
python3 tests/native_apps.py
python3 tests/cli.py
python3 tests/lifecycle.py
python3 tests/accessibility_stress.py
# Autorise uniquement le consentement dans le bureau privé créé par ce test :
python3 tests/e2e.py --compositor kwin --output artifacts/e2e-portal --portal-private-consent
```

Pour le runtime atomique distribué :

```sh
scripts/container-run.sh build
mkdir -p artifacts/container-sway
podman run --rm --read-only --network=none --userns=keep-id \
  --user "$(id -u):$(id -g)" --env CUL_TEST_ENTRY=/opt/cul/cul.py \
  --tmpfs /tmp:rw,exec,mode=1777 --tmpfs /run:rw,mode=755 \
  -v "$PWD:/work:ro" -v "$PWD/artifacts/container-sway:/results:rw" \
  --workdir /work localhost/linux-computer-use:0.1.1 \
  python3 tests/e2e.py --compositor sway --output /results
```

Remplacer sway par xvfb pour le moteur X11. Hyprland exige en plus un nœud de
rendu, par exemple `--device /dev/dri/renderD128`. Pour les deux écrans, exécuter
`python3 tests/multimonitor.py /results` dans le même conteneur. L'image est basée
sur une image Arch épinglée ; les paquets proviennent du dépôt Arch au moment de
la construction. Enregistrer son identifiant pour conserver exactement un runtime.

## Limites de la preuve

Le bureau physique n'a pas été piloté par le test du portail. GNOME, COSMIC, les
autres distributions atomiques et toutes les versions/configurations de chaque DE
n'ont pas été testés individuellement. Le bureau privé portable fournit une voie
indépendante du DE hôte ; la validation ne signifie pas un support natif uniforme
de toutes leurs API de fenêtres. Le focus natif est implémenté pour KDE, Hyprland,
Sway et X11. Les applications sans arbre accessible utilisent les pixels.

La séparation d'affichage ne constitue pas une isolation des fichiers. Dans le
runtime natif, les applications gardent les permissions de l'utilisateur. Les
applications à instance unique peuvent nécessiter un profil séparé. La topologie
des écrans est fixée à l'ouverture de session ; redémarrer après un changement.
