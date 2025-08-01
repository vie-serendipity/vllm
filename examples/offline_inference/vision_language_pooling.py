# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""
This example shows how to use vLLM for running offline inference with
the correct prompt format on vision language models for multimodal pooling.

For most models, the prompt format should follow corresponding examples
on HuggingFace model repository.
"""

from argparse import Namespace
from dataclasses import asdict
from typing import Literal, NamedTuple, Optional, TypedDict, Union, get_args

from PIL.Image import Image

from vllm import LLM, EngineArgs
from vllm.entrypoints.score_utils import ScoreMultiModalParam
from vllm.multimodal.utils import fetch_image
from vllm.utils import FlexibleArgumentParser


class TextQuery(TypedDict):
    modality: Literal["text"]
    text: str


class ImageQuery(TypedDict):
    modality: Literal["image"]
    image: Image


class TextImageQuery(TypedDict):
    modality: Literal["text+image"]
    text: str
    image: Image


class TextImagesQuery(TypedDict):
    modality: Literal["text+images"]
    text: str
    image: ScoreMultiModalParam


QueryModality = Literal["text", "image", "text+image", "text+images"]
Query = Union[TextQuery, ImageQuery, TextImageQuery, TextImagesQuery]


class ModelRequestData(NamedTuple):
    engine_args: EngineArgs
    prompt: Optional[str] = None
    image: Optional[Image] = None
    query: Optional[str] = None
    documents: Optional[ScoreMultiModalParam] = None


def run_e5_v(query: Query) -> ModelRequestData:
    llama3_template = "<|start_header_id|>user<|end_header_id|>\n\n{}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n \n"  # noqa: E501

    if query["modality"] == "text":
        text = query["text"]
        prompt = llama3_template.format(f"{text}\nSummary above sentence in one word: ")
        image = None
    elif query["modality"] == "image":
        prompt = llama3_template.format("<image>\nSummary above image in one word: ")
        image = query["image"]
    else:
        modality = query["modality"]
        raise ValueError(f"Unsupported query modality: '{modality}'")

    engine_args = EngineArgs(
        model="royokong/e5-v",
        runner="pooling",
        max_model_len=4096,
        limit_mm_per_prompt={"image": 1},
    )

    return ModelRequestData(
        engine_args=engine_args,
        prompt=prompt,
        image=image,
    )


def run_vlm2vec(query: Query) -> ModelRequestData:
    if query["modality"] == "text":
        text = query["text"]
        prompt = f"Find me an everyday image that matches the given caption: {text}"  # noqa: E501
        image = None
    elif query["modality"] == "image":
        prompt = "<|image_1|> Find a day-to-day image that looks similar to the provided image."  # noqa: E501
        image = query["image"]
    elif query["modality"] == "text+image":
        text = query["text"]
        prompt = (
            f"<|image_1|> Represent the given image with the following question: {text}"  # noqa: E501
        )
        image = query["image"]
    else:
        modality = query["modality"]
        raise ValueError(f"Unsupported query modality: '{modality}'")

    engine_args = EngineArgs(
        model="TIGER-Lab/VLM2Vec-Full",
        runner="pooling",
        max_model_len=4096,
        trust_remote_code=True,
        mm_processor_kwargs={"num_crops": 4},
        limit_mm_per_prompt={"image": 1},
    )

    return ModelRequestData(
        engine_args=engine_args,
        prompt=prompt,
        image=image,
    )


def run_jinavl_reranker(query: Query) -> ModelRequestData:
    if query["modality"] != "text+images":
        raise ValueError(f"Unsupported query modality: '{query['modality']}'")

    engine_args = EngineArgs(
        model="jinaai/jina-reranker-m0",
        runner="pooling",
        max_model_len=32768,
        trust_remote_code=True,
        mm_processor_kwargs={
            "min_pixels": 3136,
            "max_pixels": 602112,
        },
        limit_mm_per_prompt={"image": 1},
    )

    return ModelRequestData(
        engine_args=engine_args,
        query=query["text"],
        documents=query["image"],
    )


def get_query(modality: QueryModality):
    if modality == "text":
        return TextQuery(modality="text", text="A dog sitting in the grass")

    if modality == "image":
        return ImageQuery(
            modality="image",
            image=fetch_image(
                "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/American_Eskimo_Dog.jpg/360px-American_Eskimo_Dog.jpg"  # noqa: E501
            ),
        )

    if modality == "text+image":
        return TextImageQuery(
            modality="text+image",
            text="A cat standing in the snow.",
            image=fetch_image(
                "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b6/Felis_catus-cat_on_snow.jpg/179px-Felis_catus-cat_on_snow.jpg"  # noqa: E501
            ),
        )

    if modality == "text+images":
        return TextImagesQuery(
            modality="text+images",
            text="slm markdown",
            image={
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://raw.githubusercontent.com/jina-ai/multimodal-reranker-test/main/handelsblatt-preview.png"
                        },
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://raw.githubusercontent.com/jina-ai/multimodal-reranker-test/main/paper-11.png"
                        },
                    },
                ]
            },
        )

    msg = f"Modality {modality} is not supported."
    raise ValueError(msg)


def run_encode(model: str, modality: QueryModality, seed: Optional[int]):
    query = get_query(modality)
    req_data = model_example_map[model](query)

    # Disable other modalities to save memory
    default_limits = {"image": 0, "video": 0, "audio": 0}
    req_data.engine_args.limit_mm_per_prompt = default_limits | dict(
        req_data.engine_args.limit_mm_per_prompt or {}
    )

    engine_args = asdict(req_data.engine_args) | {"seed": seed}
    llm = LLM(**engine_args)

    mm_data = {}
    if req_data.image is not None:
        mm_data["image"] = req_data.image

    outputs = llm.embed(
        {
            "prompt": req_data.prompt,
            "multi_modal_data": mm_data,
        }
    )

    print("-" * 50)
    for output in outputs:
        print(output.outputs.embedding)
        print("-" * 50)


def run_score(model: str, modality: QueryModality, seed: Optional[int]):
    query = get_query(modality)
    req_data = model_example_map[model](query)

    engine_args = asdict(req_data.engine_args) | {"seed": seed}
    llm = LLM(**engine_args)

    outputs = llm.score(req_data.query, req_data.documents)

    print("-" * 30)
    print([output.outputs.score for output in outputs])
    print("-" * 30)


model_example_map = {
    "e5_v": run_e5_v,
    "vlm2vec": run_vlm2vec,
    "jinavl_reranker": run_jinavl_reranker,
}


def parse_args():
    parser = FlexibleArgumentParser(
        description="Demo on using vLLM for offline inference with "
        "vision language models for multimodal pooling tasks."
    )
    parser.add_argument(
        "--model-name",
        "-m",
        type=str,
        default="vlm2vec",
        choices=model_example_map.keys(),
        help="The name of the embedding model.",
    )
    parser.add_argument(
        "--task",
        "-t",
        type=str,
        default="embedding",
        choices=["embedding", "scoring"],
        help="The task type.",
    )
    parser.add_argument(
        "--modality",
        type=str,
        default="image",
        choices=get_args(QueryModality),
        help="Modality of the input.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Set the seed when initializing `vllm.LLM`.",
    )
    return parser.parse_args()


def main(args: Namespace):
    if args.task == "embedding":
        run_encode(args.model_name, args.modality, args.seed)
    elif args.task == "scoring":
        run_score(args.model_name, args.modality, args.seed)
    else:
        raise ValueError(f"Unsupported task: {args.task}")


if __name__ == "__main__":
    args = parse_args()
    main(args)
