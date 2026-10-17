# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: a picture vote on what a model is, for the suggest step (core/suggest.py weighs it).
#
# The suggest step renders the model from a few sides; OpenCLIP (the open-clip extra, MIT, on the CPU) scores each
# picture against a short list of plain descriptions ("a four-legged animal", "a statue on a pedestal") with no
# training for this tool, and the scores are averaged over the pictures. It is a vote, not a verdict: the
# suggester counts it beside the mesh's own measures and lets it break ties.
#
#   python autorig/core/picture_vote.py <picture.png ...>         (Blender's Python with the extras: extras.run_python)
#
# prints PICTURE_VOTE {"labels": {label: chance}, "archetypes": {archetype: chance}, "model": ...}. The weights
# (about 600 MB, the LAION-2B ViT-B/32, MIT) download once into the extras folder's models/ on first use: free, but
# it needs the network that once. Without open-clip, or with no weights, it prints PICTURE_VOTE {"error": ...}.
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL, PRETRAINED = "ViT-B-32", "laion2b_s34b_b79k"

# description: archetype it votes for. Several descriptions may vote for one archetype; their chances add.
LABELS = [
    ("a humanoid character standing upright on two legs", "humanoid"),
    ("a four-legged animal", "quadruped"),
    ("a bird", "winged"),
    ("a winged dragon", "winged"),
    ("a butterfly or a moth", "winged"),
    ("an insect", "hexapod"),
    ("a spider", "octopod"),
    ("a snake", "serpent"),
    ("a ghost floating in the air", "floater"),
    ("a statue on a pedestal", "rigid"),
    ("a vehicle", "rigid"),
]
# ("a prop, an object" took most of the vote for every sample, creatures included: a catch-all is left out)


def cache_dir():
    try:
        import extras
    except ImportError:
        sys.path.insert(0, HERE)
        import extras
    return os.path.join(extras.data_root(), "models", "open_clip")


def by_archetype(labels):
    """{description: chance} -> {archetype: summed chance}."""
    out = {}
    for text, arch in LABELS:
        if text in labels:
            out[arch] = round(out.get(arch, 0.0) + float(labels[text]), 4)
    return out


def score(paths):
    """Average zero-shot chances of each description over the pictures."""
    import torch, open_clip
    from PIL import Image
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    model, _, preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED,
                                                                 cache_dir=cache_dir())
    model.eval()
    tok = open_clip.get_tokenizer(MODEL)
    texts = ["a 3D render of " + t for t, _ in LABELS]
    with torch.no_grad():
        tf = model.encode_text(tok(texts))
        tf = tf / tf.norm(dim=-1, keepdim=True)
        imgs = torch.stack([preprocess(Image.open(p).convert("RGB")) for p in paths])
        imf = model.encode_image(imgs)
        imf = imf / imf.norm(dim=-1, keepdim=True)
        probs = (100.0 * imf @ tf.T).softmax(dim=-1).mean(dim=0).tolist()
    labels = {t: round(p, 4) for (t, _), p in zip(LABELS, probs)}
    return {"labels": labels, "archetypes": by_archetype(labels), "model": "%s/%s" % (MODEL, PRETRAINED),
            "pictures": len(paths)}


def main(argv):
    paths = [p for p in argv if os.path.isfile(p)]
    if not paths:
        res = {"error": "no pictures"}
    else:
        try:
            res = score(paths)
        except ImportError as e:
            res = {"error": "open-clip is not installed (python autorig/cli/run.py extras install open-clip): %s" % e}
        except Exception as e:                      # no network for the first download, a bad picture
            res = {"error": "%s: %s" % (type(e).__name__, e)}
    print("PICTURE_VOTE " + json.dumps(res), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
