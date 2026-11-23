"""Use the actual Mistral Search Toolkit splitters, retaining source references."""
import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from mistral_common.tokens.tokenizers.mistral import MistralTokenizer
from mistralai.search.toolkit.ingestion.text_splitters import (
    CharacterTextSplitter, TokenTextSplitter, MarkdownTextSplitter,
    MarkdownTextSplitterConfig, SeparatorTextSplitter, SeparatorTextSplitterConfig,
)


class ChunkConfig(BaseModel):
    strategy: Literal['auto', 'character', 'token', 'markdown', 'separator'] = 'markdown'
    size: int = Field(800, ge=100, le=2000)
    overlap: int = Field(80, ge=0, le=500)

    @model_validator(mode='after')
    def valid_overlap(self):
        if self.overlap >= self.size:
            raise ValueError('Overlap must be smaller than chunk size.')
        if self.strategy == 'character' and self.overlap:
            raise ValueError('The Mistral character splitter does not support overlap. Use zero.')
        return self


# 'Auto (by source type)': transcripts have no headings, so audio and video use recursive separators.
AUTO = {'audio': 'separator', 'video': 'separator'}
AUTO_DEFAULT = 'markdown'


@lru_cache(maxsize=32)
def splitter(strategy, size, overlap):
    if strategy == 'character':
        return CharacterTextSplitter(chunk_size=size)
    if strategy == 'token':
        # Toolkit's default "mistral-embed" tokenizer alias is not recognised by
        # mistral-common 1.13.1. Use its bundled v3 tokenizer without downloads.
        # The UI names this tokenizer; these are not guaranteed embedding-billing tokens.
        return TokenTextSplitter(chunk_size=size, chunk_overlap=overlap,
                                 tokenizer=MistralTokenizer.v3())
    if strategy == 'markdown':
        return MarkdownTextSplitter(MarkdownTextSplitterConfig(
            chunk_size=size, chunk_max_size=size, chunk_overlap=overlap, strip_headers=False))
    return SeparatorTextSplitter(SeparatorTextSplitterConfig(
        chunk_size=size, chunk_max_size=size, chunk_overlap=overlap,
        chunk_separators=['\n\n', '\n', '. ', ' ', '']))


def assemble(units):
    text, spans = '', []
    for unit in units:
        content = unit.get('text', '').strip()
        if not content:
            continue
        if text:
            text += '\n\n'
        start = len(text)
        text += content
        spans.append({**{k: v for k, v in unit.items() if k != 'text'},
                      'start': start, 'end': len(text)})
    return text, spans


def chunks_for(doc, config):
    if config.strategy == 'auto':
        config = config.model_copy(update={'strategy': AUTO.get(doc['kind'], AUTO_DEFAULT)})
    chunks = []
    for i, piece in enumerate(splitter(config.strategy, config.size, config.overlap).split_text(doc['text'])):
        if not piece.content.strip():
            continue
        refs = [s for s in doc['spans']
                if s['end'] > piece.start_offset and s['start'] < piece.end_offset]
        key = f"{doc['id']}:{config.model_dump_json()}:{i}:{piece.content}"
        chunks.append({'id': hashlib.sha256(key.encode()).hexdigest()[:20],
                       'document_id': doc['id'], 'filename': doc['name'], 'text': piece.content,
                       'start': piece.start_offset, 'end': piece.end_offset, 'strategy': config.strategy, 'references': refs})
    return chunks
