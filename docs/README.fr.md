<p align="center"><img src="assets/hero.png" alt="Linux Computer Use — votre bureau, votre agent, en parallèle" width="100%"></p>

<p align="center"><strong>Donnez à votre agent un vrai bureau Linux. Gardez le vôtre disponible.</strong></p>
<p align="center"><a href="../README.md">English / Page principale</a> · <a href="INSTALLATION.md">Installation détaillée</a> · <a href="VALIDATION.md">Preuves E2E</a></p>

## Ce que fait le projet

Linux Computer Use est un skill autonome et un contrôleur natif pour les applications Linux : captures d’écran, accessibilité AT-SPI, fenêtres, souris, clavier, Unicode et actions groupées. Les **20 mêmes outils** sont disponibles par MCP stdio et CLI JSON. Le contrôleur ne dépend ni de Codex, ni d’un fournisseur de modèles, ni d’une clé API.

Son mode **bureau privé** crée un écran, un compositeur, un focus, un presse-papiers et des bus de session indépendants. L’agent peut travailler dans ses applications pendant que vous continuez à utiliser les vôtres. Le mode **bureau courant** lui permet plutôt d’agir dans les fenêtres déjà ouvertes, en partageant votre focus et votre curseur.

Cette version `0.1.1` est une préversion : une base de treize rapports E2E passe sur SteamOS et dans de vrais bureaux privés/conteneurisés. La détection du bureau ne certifie pas toutes les configurations matérielles.

## Installer

Pendant que le dépôt est privé, utilisez un client GitHub authentifié :

```sh
gh repo clone NuCl34R/computer-use-linux
cd computer-use-linux
./install.sh --dry-run
./install.sh
~/.local/bin/linux-computer-use doctor
```

`git clone https://github.com/NuCl34R/computer-use-linux.git` fonctionne aussi avec une authentification Git configurée. L’installation demande Python système 3.10+ et s’exécute avec votre utilisateur habituel.

L’installateur détecte la distribution, sa famille, le DE, Wayland/X11, l’architecture, Omarchy, les systèmes immuables, les bibliothèques natives, les compositeurs et Podman. Sur le SteamOS testé, il sélectionne KWin déjà présent, sans modification de l’OS.

Sur une distribution mutable avec des dépendances manquantes :

```sh
./install.sh --install-deps
```

Les recettes couvrent Arch/Omarchy, Debian/Ubuntu, Fedora et openSUSE. Le gestionnaire de paquets conserve sa confirmation habituelle. Gardez Arch à jour avec votre procédure normale avant d’installer ; le script ne met pas l’OS à niveau.

Sur un système immuable, ou pour un environnement graphique autonome avec Podman rootless :

```sh
./install.sh --runtime container --build-container
```

La première construction télécharge plusieurs Go de composants. Les sessions suivantes réutilisent l’image. Le bureau privé Sway fonctionne sans GPU. Le script ne déverrouille jamais SteamOS et n’installe pas de paquets dans une image système immuable.

Les applications du conteneur doivent être installées dans son image. `CUL_WORKDIR` sélectionne le répertoire partagé ; `CUL_NETWORK=host` active le réseau si nécessaire. Pour piloter les fenêtres existantes du poste, utilisez le runtime natif.

## Brancher un agent

L’installateur affiche les chemins exacts. Le format MCP courant ressemble à ceci :

```json
{
  "mcpServers": {
    "linux-computer-use": {
      "command": "/home/VOTRE_UTILISATEUR/.local/bin/linux-computer-use",
      "args": ["mcp"]
    }
  }
}
```

Certains harnesses utilisent un format de configuration différent ; la commande et les arguments restent les mêmes. Le lanceur choisit le runtime installé. Aucun fichier de configuration existant du harness n’est réécrit.

```sh
./install.sh --harness claude
./install.sh --harness agents
./install.sh --skills-dir "$HOME/chemin/vers/skills"
```

Le [skill](../skills/linux-computer-use/SKILL.md) apprend à l’agent à observer, agir et vérifier. Un premier essai :

> Utilise Linux Computer Use pour ouvrir une application dans un bureau privé, saisir du français et des emoji, vérifier le résultat avec une nouvelle capture, puis fermer la session. Je veux continuer à utiliser mon bureau pendant ce temps.

Sans MCP, la CLI peut garder un contrôleur actif sur un socket Unix privé et recevoir des appels JSON. Voir [les exemples de la page principale](../README.md#json-cli-a-persistent-desktop-from-any-process-tool).

## Ce qui est testé

| Chemin | Résultat |
| --- | --- |
| SteamOS 3.8.26, KWin privé natif | Suite MCP/GUI complète réussie |
| Portail KDE dans un KWin privé | Consentement réel, capture et entrée réussis |
| Hyprland privé dans un conteneur Arch | Entrée/capture Wayland réussies |
| Sway/Pixman et Xvfb rootless | Suites complètes, racine en lecture seule, sans GPU ni réseau |
| Qt, XWayland, Unicode | Texte reçu et callbacks vérifiés |
| Deux écrans, échelle 125 % | Coordonnées de clic vérifiées |
| Arrêts, annulation, accessibilité instable | Nettoyage et contrats vérifiés |
| Ubuntu 24.04 dans GitHub Actions | Suites complètes Sway et Xvfb réussies |
| Installateur universel | Détection, chemins avec espaces, sauvegarde, rollback et installation indépendante |

Les captures réelles sont accessibles dans [la page principale](../README.md#tested-on-real-desktops). La bannière est une illustration générée ; elle n’est pas une preuve de fonctionnement.

La capture KWin native atteint une médiane de **2,13 ms** à chaud ; une action suivie d’une image modifiée et vérifiée prend **53,83 ms** dans la mesure initiale. Ces mesures portent sur une seule machine et excluent l’inférence du LLM. **La parité macOS/Windows n’a pas été mesurée.** [Méthode complète et résultats →](../skills/linux-computer-use/references/validation.md)

## Mise à jour et désinstallation

```sh
./install.sh --update
./install.sh --uninstall --dry-run
./install.sh --uninstall
```

Réutilisez les mêmes options de répertoires/harness. La mise à jour garde des sauvegardes horodatées. La désinstallation s’appuie sur un manifeste et refuse de supprimer des fichiers modifiés. Les sauvegardes, les images Podman et vos documents sont conservés.

## Limites utiles

Le bureau privé sépare l’affichage et l’entrée ; il ne constitue pas un bac à sable de fichiers. Les applications natives gardent les permissions de votre utilisateur. Le mode courant partage le focus et le presse-papiers. Le portail demande un consentement réel. Les applications sans accessibilité restent utilisables par pixels.

Utilisez le `frame_id` de la capture pour les coordonnées ; le contrôleur applique l’échelle. Redémarrez la session après un changement d’écrans. Après `stop`, démarrez un nouveau serveur pour une nouvelle session.

Ce projet fournit un skill Linux autonome et un serveur MCP utilisable avec différents agents. Les [tests de validation](VALIDATION.md) permettent de documenter les résultats sur chaque bureau et configuration. La publication relève du propriétaire du dépôt.

[Installation](INSTALLATION.md) · [Architecture](../skills/linux-computer-use/references/architecture.md) · [Contribution](../CONTRIBUTING.md) · [Sécurité](../SECURITY.md) · [Licence MIT](../LICENSE)
