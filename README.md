# peertube_to_article.py

Crée un article Grav à partir d'une vidéo PeerTube : la vidéo est intégrée avec le [plugin Grav PeerTube](https://github.com/HackTechDev/grav_plugin_peertube), sa description devient le corps de l'article, et une entrée est ajoutée dans `user/pages/01.carnet/blog.md`.

## Prérequis

- Python 3.8 ou plus récent (bibliothèque standard uniquement, rien à installer)
- Le plugin Grav `peertube` installé sur le site
- Une vidéo publique (ou non listée) sur une instance PeerTube

## Installation

Créer un lien vers le script dans le dossier `scripts/` du site Grav :

```sh
ln -s "$PWD/peertube_to_article.py" /chemin/du/site/scripts/peertube_to_article.py
```

Le script trouve seul la racine du site (le dossier qui contient `user/pages`), depuis son propre emplacement ou le répertoire courant.

## Utilisation

Depuis la racine du site Grav (ou un de ses sous-dossiers) :

```sh
python3 scripts/peertube_to_article.py ID_VIDEO REPERTOIRE_SECTION [options]
```

| Paramètre | Description |
|---|---|
| `ID_VIDEO` | Identifiant de la vidéo (`cw5EuTUeyHhaKtZyc5S1R9` ou UUID), ou son adresse (`https://instance/w/…`, `…/videos/watch/…`, `…/videos/embed/…`) |
| `REPERTOIRE_SECTION` | Répertoire de destination de l'article, sous `user/pages` (ex. `user/pages/04.jeu-libre`) |

## Options

| Option | Défaut | Description |
|---|---|---|
| `--instance URL` | `https://peertube.hacktech.dev` | Instance PeerTube (ignorée si `ID_VIDEO` est une adresse) |
| `--title TITRE` | titre de la vidéo | Titre de l'article |
| `--slug SLUG` | titre sans accents ni ponctuation | Nom du dossier de l'article, sans le numéro |
| `--tags A B C` | tags de la vidéo, sinon `Peertube` | Tags de l'article |
| `--date JJ-MM-AAAA` | aujourd'hui | Date de l'article et de l'entrée dans `blog.md` |
| `--no-image` | | Ne pas copier la miniature de la vidéo |
| `--no-blog` | | Ne pas modifier `blog.md` |
| `--dry-run` | | Afficher l'article et l'entrée `blog.md` sans rien écrire |

Le titre d'une vidéo est souvent un nom de fichier (ex. `hexapod_v6-2026-07-05_17.54.35`) : il vaut mieux préciser `--title`.

## Exemples

```sh
# Aperçu, sans rien écrire
python3 scripts/peertube_to_article.py 48EhAFiVFBqDmg1SdQPBap user/pages/07.appli --dry-run

# Création avec un titre et des tags
python3 scripts/peertube_to_article.py https://peertube.hacktech.dev/w/48EhAFiVFBqDmg1SdQPBap \
    user/pages/07.appli --title "Code Lisa" --tags Claude "Weird Science"
```

## Ce que fait le script

1. **Récupération** : lit les informations de la vidéo (titre, description, tags, miniature) via l'API de l'instance (`/api/v1/videos/<id>`).
2. **Dossier de l'article** : crée `<numéro suivant>.<slug>/item.md` dans la section.
3. **En-tête** : même forme que les autres articles (`title`, `date`, `publish_date`, `taxonomy.tag`, `metadata`…).
4. **Image** : la miniature de la vidéo est copiée dans le dossier de l'article sous le nom `<slug>.jpg`. Elle sert de `hero_image` et de `og:image`.
5. **Corps** : section `#### Vidéo.` avec `[plugin:peertube](https://instance/w/<id>)`, puis `#### Description.` avec la description de la vidéo (omise si elle est vide). Les adresses nues deviennent des liens et chaque ligne se termine par un retour à la ligne Markdown ; les blocs de code et le code inline ne sont pas modifiés.
6. **`blog.md`** : ajoute `* JJ/MM/AA : [Titre](/section/slug)` à sa place chronologique, en créant le titre du mois ou de l'année si besoin.

## Sécurités

Le script s'arrête sans rien écrire si :

- la vidéo a déjà un article dans la section (son identifiant ou son UUID apparaît dans un `item.md`) ;
- un article avec le même slug existe déjà dans la section ;
- le répertoire n'existe pas ou n'est pas sous `user/pages` ;
- l'identifiant est invalide, la vidéo est introuvable ou privée, ou l'instance ne répond pas ;
- `blog.md` est illisible (sauf avec `--no-blog`).

Si une écriture échoue en cours de route, le dossier de l'article est supprimé. `blog.md` est écrit dans un fichier temporaire puis renommé.

## Après la création

Le script ne fait ni commit ni mise en ligne. Ensuite :

```sh
git add user/pages
git commit -m "[Add] Mon titre"
./pushGit.sh
./syncUser.sh
```
