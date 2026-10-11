#!/usr/bin/env python3
"""
Cree un article Grav a partir d'une video PeerTube : la video est integree
avec le plugin Grav peertube, sa description devient le corps de l'article,
et une entree est ajoutee dans user/pages/01.carnet/blog.md.

Usage:
    python3 scripts/peertube_to_article.py ID_VIDEO REPERTOIRE_SECTION [options]

Exemples:
    python3 scripts/peertube_to_article.py cw5EuTUeyHhaKtZyc5S1R9 user/pages/04.jeu-libre
    python3 scripts/peertube_to_article.py https://peertube.hacktech.dev/w/cw5EuTUeyHhaKtZyc5S1R9 \
        user/pages/04.jeu-libre --title "Brutal Doom Voxel" --tags Doom Voxel --dry-run
"""

import argparse
import datetime
import json
import re
import shutil
import socket
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_INSTANCE = "https://peertube.hacktech.dev"


# ---------------------------------------------------------------------------
# Recherche automatique de la racine du site Grav
# ---------------------------------------------------------------------------

def find_grav_root():
    """
    Recherche la racine du site Grav.

    La racine est identifiee par la presence du repertoire :
        user/pages

    Cela permet de lancer le script depuis :
        - la racine du site ;
        - le repertoire scripts/ ;
        - un autre repertoire situe sous la racine ;
        - ou avec un chemin absolu vers le script.
    """

    script_path = Path(__file__).resolve()
    current_path = Path.cwd().resolve()

    candidates = []

    # Repertoire du script et tous ses parents.
    candidates.extend([script_path.parent, *script_path.parents])

    # Repertoire courant et tous ses parents.
    candidates.extend([current_path, *current_path.parents])

    # Supprime les doublons tout en conservant l'ordre.
    seen = set()

    for path in candidates:
        path = path.resolve()

        if path in seen:
            continue

        seen.add(path)

        if (path / "user" / "pages").is_dir():
            return path

    sys.exit(
        "Impossible de trouver la racine du site Grav "
        "(repertoire 'user/pages' introuvable)."
    )


# Definis dans main(), apres la lecture des arguments :
# --help fonctionne ainsi hors d'un site Grav.
ROOT = None
PAGES_DIR = None
BLOG_INDEX = None


def set_grav_root():
    global ROOT, PAGES_DIR, BLOG_INDEX

    ROOT = find_grav_root()
    PAGES_DIR = ROOT / "user" / "pages"
    BLOG_INDEX = PAGES_DIR / "01.carnet" / "blog.md"


MOIS = [
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
]


NUM_PREFIX_RE = re.compile(r"^(\d+)\.")

# Titres et entrees de blog.md :
# ### Année 2026
# #### Octobre / #### Juin 2024 : / ##### Décembre 2023
# * 01/10/26 : [Titre](/route)
YEAR_RE = re.compile(r"^###\s+Année\s+(\d{4})\s*$")
MONTH_RE = re.compile(r"^#{4,5}\s+(\w+)")
ENTRY_RE = re.compile(r"^\*\s+(\d{2})/(\d{2})/(\d{2})\b")

# Identifiant PeerTube : shortUUID (22 caracteres) ou UUID.
VIDEO_ID_RE = re.compile(
    r"^([0-9A-Za-z]{22}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.I
)

# Adresse d'une video :
# https://instance/w/ID, /videos/watch/ID, /videos/embed/ID
VIDEO_URL_RE = re.compile(
    r"^(https?://[^/\s]+)/(?:w|videos/watch|videos/embed)/([^/?#\s]+)"
)

# Adresse nue dans la description (hors lien Markdown ou <...>).
BARE_URL_RE = re.compile(
    r"(?<![(\[<\"'=])\bhttps?://[^\s<>()\[\]]+"
)

# Code inline : `code` ou ``code``
INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")


FRONTMATTER = """---
title: "{title}"
date: '{date} 00:00'
publish_date: '{date} 00:00'
dateformat: 'd-m-Y H:i'
hero_classes: 'text-light title-h1h2 overlay-dark-gradient hero-large parallax'
{hero_image}show_sidebar: true
content:
    items: '- ''@self.children'''
    limit: '5'
    order:
        by: date
        dir: desc
    pagination: '1'
    url_taxonomy_filters: '1'
taxonomy:
    tag:
{tags}
feed:
    limit: 10
metadata:
    description: "{title}"
{og_image}published: true
---

#### Vidéo.

[plugin:peertube]({video_url})

"""


# ---------------------------------------------------------------------------
# Gestion IPv4 / IPv6
# ---------------------------------------------------------------------------

_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_first(*args, **kwargs):
    # L'IPv6 peut rester bloquee jusqu'au timeout :
    # on essaie l'IPv4 d'abord.
    return sorted(
        _getaddrinfo(*args, **kwargs),
        key=lambda a: a[0] != socket.AF_INET
    )


def prefer_ipv4():
    """
    Appelee dans main() : importer le module ne modifie pas le reseau.
    """

    socket.getaddrinfo = _getaddrinfo_ipv4_first


# ---------------------------------------------------------------------------
# PeerTube
# ---------------------------------------------------------------------------

def peertube_get(url: str, binary: bool = False):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "peertube_to_article",
            "Accept": "*/*" if binary else "application/json",
        }
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()

    if binary:
        return data

    return json.loads(data.decode("utf-8", errors="replace"))


def parse_video(arg: str, instance: str):
    """
    Renvoie (instance, identifiant) a partir d'un identifiant
    ou d'une adresse de video (qui impose alors son instance).
    """

    arg = arg.strip()
    m = VIDEO_URL_RE.match(arg)

    if m:
        instance, arg = m.group(1), m.group(2)

    if not VIDEO_ID_RE.match(arg):
        sys.exit(
            f"Identifiant de video PeerTube invalide : {arg}"
        )

    return instance.rstrip("/"), arg


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    for lig, rep in (
        ("œ", "oe"),
        ("Œ", "Oe"),
        ("æ", "ae"),
        ("Æ", "Ae"),
    ):
        text = text.replace(lig, rep)

    text = unicodedata.normalize(
        "NFKD",
        text
    )

    text = "".join(
        c
        for c in text
        if not unicodedata.combining(c)
    ).lower()

    text = re.sub(
        r"[^a-z0-9-]+",
        "_",
        text
    )

    return re.sub(
        r"_+",
        "_",
        text
    ).strip("_-")


def escape_md_link(text: str) -> str:
    """
    Echappe les crochets du texte d'un lien Markdown.
    """

    return re.sub(r"([\[\]])", r"\\\1", text)


def yaml_escape(text: str) -> str:
    return text.replace(
        "\\",
        "\\\\"
    ).replace(
        '"',
        '\\"'
    )


def fenced_lines(text: str):
    """
    Renvoie (ligne, dans_un_bloc_de_code) pour chaque ligne.
    """

    fence = None

    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()

        if fence is None and stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            yield line, True

        elif fence and stripped.startswith(fence):
            fence = None
            yield line, True

        else:
            yield line, fence is not None


# ---------------------------------------------------------------------------
# Transformation de la description
# ---------------------------------------------------------------------------

def format_description(text: str) -> str:
    """
    Met en forme la description PeerTube (Markdown, retours a la ligne
    simples affiches tels quels) pour Grav :
        - les adresses nues deviennent des liens [url](url) ;
        - chaque ligne non vide se termine par deux espaces
          (retour a la ligne Markdown).
    Les blocs de code et le code inline ne sont pas modifies.
    """

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def link(m):
        url = m.group(0)

        # La ponctuation finale ne fait pas partie de l'adresse.
        end = re.search(r"[.,;:!?]+$", url)
        tail = end.group(0) if end else ""
        url = url[:len(url) - len(tail)]

        return f"[{url}]({url}){tail}"

    out = []

    for line, in_code in fenced_lines(text + "\n"):
        line = line.rstrip("\n")

        if not in_code:
            # Le code inline (`...`) n'est pas modifie.
            out_line, pos = [], 0

            for m in INLINE_CODE_RE.finditer(line):
                out_line += [
                    BARE_URL_RE.sub(link, line[pos:m.start()]),
                    m.group(0)
                ]
                pos = m.end()

            line = "".join(out_line) + BARE_URL_RE.sub(link, line[pos:])

            if line.strip():
                line = line.rstrip() + "  "

        out.append(line)

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Grav
# ---------------------------------------------------------------------------

def next_number(section: Path) -> int:
    nums = [
        int(m.group(1))
        for d in section.iterdir()
        if d.is_dir()
        and (m := NUM_PREFIX_RE.match(d.name))
    ]

    return max(
        nums,
        default=0
    ) + 1


def section_route(section: Path) -> str:
    rel = section.resolve().relative_to(
        PAGES_DIR.resolve()
    )

    return "/" + "/".join(
        NUM_PREFIX_RE.sub("", p)
        for p in rel.parts
    )


def add_to_blog_index(
    text: str,
    entry: str,
    day: datetime.date
) -> str:
    """
    Insere l'entree a sa place chronologique (du plus recent au plus
    ancien), en creant annee/mois si besoin. Le jour meme, l'entree
    passe avant les entrees existantes.
    """

    lines = text.split("\n")

    year_title = f"### Année {day.year}"
    month_title = f"#### {MOIS[day.month - 1]}"

    def after_text(start, end):
        """Index qui suit la derniere ligne non vide de [start, end)."""
        i = end
        while i > start and not lines[i - 1].strip():
            i -= 1
        return i

    def insert_block(before, start, end, block):
        """
        Insere un bloc avant le titre `before`, ou a la fin
        de [start, end) si `before` vaut None.
        """
        if before is not None:
            lines[before:before] = block + ["", ""]
        else:
            k = after_text(start, end)
            lines[k:k] = ["", ""] + block

    # Années : (index, année), de la plus récente à la plus ancienne.
    years = [
        (i, int(m.group(1)))
        for i, line in enumerate(lines)
        if (m := YEAR_RE.match(line))
    ]

    year_idx = next((i for i, y in years if y == day.year), None)

    # L'année n'existe pas encore : avant la première année plus ancienne.
    if year_idx is None:
        insert_block(
            next((i for i, y in years if y < day.year), None),
            0,
            len(lines),
            [year_title, "", month_title, "", entry]
        )
        return "\n".join(lines)

    end = next((i for i, _ in years if i > year_idx), len(lines))

    # Mois de l'année : (index, numéro du mois).
    months = [
        (i, MOIS.index(m.group(1)) + 1)
        for i in range(year_idx + 1, end)
        if (m := MONTH_RE.match(lines[i])) and m.group(1) in MOIS
    ]

    month_idx = next((i for i, n in months if n == day.month), None)

    # Le mois n'existe pas encore : avant le premier mois plus ancien.
    if month_idx is None:
        insert_block(
            next((i for i, n in months if n < day.month), None),
            year_idx + 1,
            end,
            [month_title, "", entry]
        )
        return "\n".join(lines)

    month_end = next((i for i, _ in months if i > month_idx), end)

    # Entrées du mois : (index, date).
    entries = []

    for i in range(month_idx + 1, month_end):
        m = ENTRY_RE.match(lines[i])
        if not m:
            continue
        try:
            entries.append((i, datetime.date(
                2000 + int(m.group(3)), int(m.group(2)), int(m.group(1))
            )))
        except ValueError:
            continue

    # Avant la première entrée du même jour ou plus ancienne,
    # sinon après la dernière entrée du mois.
    insert = next((i for i, d in entries if d <= day), None)

    if insert is None:
        insert = (
            entries[-1][0] + 1
            if entries
            else after_text(month_idx + 1, month_end)
        )

        if not entries:
            lines[insert:insert] = ["", entry]
            return "\n".join(lines)

    lines.insert(insert, entry)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Cree un article Grav a partir "
            "d'une video PeerTube."
        )
    )

    parser.add_argument(
        "video_id",
        help=(
            "Identifiant de la video PeerTube (shortUUID ou UUID) "
            "ou son adresse (https://instance/w/ID)"
        )
    )

    parser.add_argument(
        "section",
        help=(
            "Repertoire de destination "
            "(ex. user/pages/04.jeu-libre)"
        )
    )

    parser.add_argument(
        "--instance",
        default=DEFAULT_INSTANCE,
        help=(
            "Instance PeerTube "
            f"(defaut : {DEFAULT_INSTANCE} ; "
            "ignore si video_id est une adresse)"
        )
    )

    parser.add_argument(
        "--title",
        help=(
            "Titre de l'article "
            "(defaut : titre de la video)"
        )
    )

    parser.add_argument(
        "--slug",
        help=(
            "Nom du dossier sans numero "
            "(defaut : titre sans accents)"
        )
    )

    parser.add_argument(
        "--tags",
        nargs="*",
        help=(
            "Tags "
            "(defaut : tags de la video PeerTube)"
        )
    )

    parser.add_argument(
        "--date",
        help=(
            "Date de l'article JJ-MM-AAAA "
            "(defaut : aujourd'hui)"
        )
    )

    parser.add_argument(
        "--no-image",
        action="store_true",
        help="Ne pas copier la miniature de la video"
    )

    parser.add_argument(
        "--no-blog",
        action="store_true",
        help="Ne pas modifier blog.md"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche le resultat sans rien ecrire"
    )

    args = parser.parse_args()

    set_grav_root()
    prefer_ipv4()

    # -----------------------------------------------------------------------
    # Répertoire de destination
    #
    # Un chemin relatif est toujours relatif à la racine Grav.
    # -----------------------------------------------------------------------

    section = Path(args.section)

    if not section.is_absolute():
        section = ROOT / section

    section = section.resolve()

    if not section.is_dir():
        sys.exit(
            f"Repertoire introuvable : {section}"
        )

    # Vérifie que le répertoire est bien sous user/pages.
    try:
        route = section_route(section)

    except ValueError:
        sys.exit(
            f"Le repertoire doit etre sous {PAGES_DIR}"
        )

    # -----------------------------------------------------------------------
    # Date
    # -----------------------------------------------------------------------

    try:
        day = (
            datetime.datetime.strptime(
                args.date,
                "%d-%m-%Y"
            ).date()
            if args.date
            else datetime.date.today()
        )

    except ValueError:
        parser.error(
            f"date invalide : {args.date} (format attendu : JJ-MM-AAAA)"
        )

    # -----------------------------------------------------------------------
    # Récupération PeerTube
    # -----------------------------------------------------------------------

    instance, video_id = parse_video(
        args.video_id,
        args.instance
    )

    try:

        info = peertube_get(
            f"{instance}/api/v1/videos/"
            f"{urllib.parse.quote(video_id)}"
        )

    except urllib.error.HTTPError as e:

        hint = (
            " (video introuvable ou privee)"
            if e.code in (400, 401, 403, 404)
            else ""
        )

        sys.exit(
            f"Erreur PeerTube ({e.code}) "
            f"pour {video_id} : {e.reason}{hint}"
        )

    except urllib.error.URLError as e:

        sys.exit(
            f"Impossible de joindre {instance} : "
            f"{e.reason}"
        )

    except TimeoutError:

        sys.exit(
            f"{instance} ne repond pas (delai depasse)."
        )

    except json.JSONDecodeError:

        sys.exit(
            f"Reponse inattendue de {instance} : "
            f"est-ce bien une instance PeerTube ?"
        )

    short_uuid = info.get("shortUUID") or video_id
    uuid = info.get("uuid") or video_id
    video_url = f"{instance}/w/{short_uuid}"

    # -----------------------------------------------------------------------
    # Vérification des doublons
    #
    # La video a deja un article si son shortUUID ou son UUID
    # apparait dans un item.md de la section.
    # -----------------------------------------------------------------------

    for md in section.glob("*/item.md"):
        text = md.read_text(encoding="utf-8", errors="replace")

        if short_uuid in text or uuid.lower() in text.lower():
            sys.exit(
                f"Cette video a deja un article : "
                f"{md.relative_to(ROOT)}"
            )

    # -----------------------------------------------------------------------
    # Informations de la vidéo
    # -----------------------------------------------------------------------

    title = (
        args.title
        or info.get("name")
        or short_uuid
    )

    tags = (
        args.tags
        if args.tags is not None
        else info.get("tags") or []
    )

    description = (info.get("description") or "").strip()

    # Un titre sans caractere latin donne un slug vide :
    # on se rabat sur l'identifiant de la video.
    slug = (
        args.slug
        or slugify(title)
        or slugify(short_uuid)
        or "article"
    )

    # -----------------------------------------------------------------------
    # Vérification du slug
    # -----------------------------------------------------------------------

    if any(
        NUM_PREFIX_RE.sub("", d.name) == slug
        for d in section.iterdir()
        if d.is_dir()
    ):
        sys.exit(
            f"Un article '{slug}' existe deja "
            f"dans {section}"
        )

    # -----------------------------------------------------------------------
    # Nom du répertoire de l'article
    # -----------------------------------------------------------------------

    article_dir = (
        section
        / f"{next_number(section)}.{slug}"
    )

    # -----------------------------------------------------------------------
    # Image de l'article
    #
    # La miniature de la video (previewPath, la plus grande) est copiee
    # dans le dossier de l'article et sert de hero_image et de og:image.
    # -----------------------------------------------------------------------

    image_path = (
        None
        if args.no_image
        else info.get("previewPath") or info.get("thumbnailPath")
    )

    image_name = None
    image_data = None

    if image_path:
        ext = Path(urllib.parse.urlparse(image_path).path).suffix.lower()
        image_name = f"{slug}{ext or '.jpg'}"

        if not args.dry_run:
            try:
                image_data = peertube_get(
                    urllib.parse.urljoin(instance + "/", image_path),
                    binary=True
                )

            except (urllib.error.URLError, TimeoutError) as e:
                print(
                    f"Miniature non telechargee ({e}) : "
                    f"article cree sans image."
                )
                image_name = None

    hero_image = (
        f"hero_image: {image_name}\n"
        if image_name
        else ""
    )

    og_image = (
        f'    "og:image": "https://labo.hacktech.dev/user/pages/'
        f'{article_dir.relative_to(PAGES_DIR).as_posix()}/{image_name}"\n'
        if image_name
        else ""
    )

    # -----------------------------------------------------------------------
    # Tags YAML
    # -----------------------------------------------------------------------

    tags_yaml = "\n".join(
        f'        - "{yaml_escape(t)}"'
        for t in tags
    ) or "        - Peertube"

    # -----------------------------------------------------------------------
    # Génération de l'article
    # -----------------------------------------------------------------------

    content = FRONTMATTER.format(
        title=yaml_escape(title),
        date=day.strftime("%d-%m-%Y"),
        tags=tags_yaml,
        hero_image=hero_image,
        og_image=og_image,
        video_url=video_url,
    )

    if description:
        content += (
            "\n#### Description.\n\n"
            + format_description(description)
        )

    # -----------------------------------------------------------------------
    # Entrée blog.md
    # -----------------------------------------------------------------------

    entry = (
        f"* {day.strftime('%d/%m/%y')} : "
        f"[{escape_md_link(title)}]({route}/{slug})"
    )

    # blog.md est lu avant toute ecriture : s'il est illisible,
    # on s'arrete sans avoir cree l'article.
    blog_text = None

    if not args.no_blog:
        try:
            blog_text = add_to_blog_index(
                BLOG_INDEX.read_text(encoding="utf-8"),
                entry,
                day
            )

        except (OSError, UnicodeDecodeError) as e:
            sys.exit(
                f"Impossible de lire "
                f"{BLOG_INDEX.relative_to(ROOT)} : {e}"
            )

    # -----------------------------------------------------------------------
    # Mode dry-run
    # -----------------------------------------------------------------------

    if args.dry_run:

        print(
            f"== {article_dir.relative_to(ROOT)}/item.md ==\n"
            f"{content}"
        )

        print(
            f"== Image ==\n"
            f"{image_path + ' -> ' + image_name if image_name else 'aucune'}"
        )

        if not args.no_blog:

            print(
                f"== Entree blog.md ==\n"
                f"{entry}"
            )

        return

    # -----------------------------------------------------------------------
    # Création de l'article et mise à jour du blog
    #
    # En cas d'erreur, le dossier de l'article est supprime.
    # blog.md est ecrit dans un fichier temporaire puis renomme,
    # pour ne jamais rester a moitie ecrit.
    # -----------------------------------------------------------------------

    article_dir.mkdir()

    try:
        (article_dir / "item.md").write_text(content, encoding="utf-8")

        if image_data:
            (article_dir / image_name).write_bytes(image_data)

        if blog_text is not None:
            tmp = BLOG_INDEX.with_name(BLOG_INDEX.name + ".tmp")
            tmp.write_text(blog_text, encoding="utf-8")
            tmp.replace(BLOG_INDEX)

    except OSError as e:
        shutil.rmtree(article_dir, ignore_errors=True)
        sys.exit(f"Erreur d'ecriture, article annule : {e}")

    print(
        f"Article cree : "
        f"{article_dir.relative_to(ROOT)}/item.md"
    )

    if image_data:
        print(f"Image copiee : {image_name}")

    if not args.no_blog:

        print(
            f"Entree ajoutee dans "
            f"{BLOG_INDEX.relative_to(ROOT)} : "
            f"{entry}"
        )


if __name__ == "__main__":
    main()
