#!/usr/bin/env python
import logging
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from glob import glob

import feedgenerator
import requests
from scipy.special import softmax

logging.basicConfig()
log = logging.getLogger(__name__)


def main():
    harvest_since_last_modification()

    entries = list(iter_load_entries_from_xml())
    if not entries:
        log.error("No new entries, is it the weekend?")
        return

    texts = [
        single_line(entry["title"] + " abstract: " + entry["abstract"])
        for entry in entries
    ]

    model = load_model()
    entries = (
        *model.predict(texts),
        texts,
        entries,
    )  # prediction label, score, arxiv text, arxiv label

    feed = feedgenerator.Rss201rev2Feed(
        title="arXiv misclassified: all",
        link="http://export.arxiv.org/rss/",
        description="Papers from arXiv that should be classifed cs.SE according to our model.",
        language="en",
    )
    sub_categories = ("cs.AI", "cs.LG", "stat.ML")
    sub_categories_str = " ".join(sub_categories)
    feed_sub = feedgenerator.Rss201rev2Feed(
        title="arXiv misclassified: " + sub_categories_str,
        link="http://export.arxiv.org/rss/",
        description="Papers from "
        + sub_categories_str
        + " that should be classifed cs.SE according to our model.",
        language="en",
    )

    for pred, score, text, entry in zip(*entries):
        label = entry["categories"]
        if pred and "cs.se" not in label.lower() and "cs.pl" not in label.lower():
            abs_link = entry["link"]
            abstract = entry["abstract"]
            authors = entry["authors"]
            pdf_link = abs_link.replace("/abs/", "/pdf/")
            score = softmax(score)
            title = entry["title"]

            r = requests.get(abs_link)
            if r.ok:
                description = r.text
            else:
                description = f"""
                {abstract}
                <p>Authors: {authors}
                <p><a href="{pdf_link}">{pdf_link}</a>
                <p><a href="{abs_link}">{abs_link}</a>
                <p>Categories: {label}
                <p>score: {score[1]:.2f}
                """.strip()

            args = dict(
                title=title,
                link=pdf_link,
                description=description,
                unique_id=pdf_link,
                categories=label.split(),
            )
            feed.add_item(**args)
            if any(sub in label for sub in sub_categories):
                feed_sub.add_item(**args)

    os.makedirs("feed", exist_ok=True)
    with open("feed/feed.xml", "w") as f:
        print(feed.writeString("utf-8"), file=f)
    with open("feed/feed2.xml", "w") as f:
        print(feed_sub.writeString("utf-8"), file=f)


def harvest_since_last_modification():
    try:
        date = datetime.fromtimestamp(os.stat("feed/feed.xml").st_mtime)
    except OSError:
        log.exception("Got OSError when trying to stat feed file:")
        date = datetime.today()
    date = date.strftime("%Y-%m-%d")
    log.info("Harvesting since %s", date)
    subprocess.run(
        f"rm -rf data && mkdir data && cd data && oai-harvest 'http://export.arxiv.org/oai2' --from {date} -p arXiv",
        check=True,
        shell=True,
    )


def iter_load_entries_from_xml():
    tags = ("abstract", "authors", "categories", "id", "title")

    for fname in glob("data/*.xml"):
        root = ET.parse(fname).getroot()
        d = {}
        for el in root:
            tag = el.tag
            for wanted_tag in tags:
                if tag.endswith(wanted_tag):
                    d[wanted_tag] = el_text(el)

        if all(tag in d for tag in tags):  # Sanity check: valid entry
            d["link"] = f"https://arxiv.org/abs/{d['id']}"
            yield d
        else:
            log.warning(
                "File %s is not complete, contains keys: %s", fname, list(d.keys())
            )


def el_text(el):
    if not el.tag.endswith("authors"):
        return el.text.strip()
    return " - ".join(author_names_text(el))


def author_names_text(el):
    for child in el:
        yield " ".join(child.itertext())


def single_line(s):
    return " ".join(s.split())


def load_model():
    from simpletransformers.classification import ClassificationModel

    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    log.debug("Loading ClassificationModel")
    return ClassificationModel(
        "roberta",
        "outputs/",
        use_cuda=False,
        args={"train_batch_size": 64, "eval_batch_size": 64, "process_count": 8},
    )


if __name__ == "__main__":
    main()
