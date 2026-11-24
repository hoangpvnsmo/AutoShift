"""Checkpoint migration and persistence policy; same schema and real/demo paths."""
from dataclasses import replace
from pathlib import Path
from core.engine.checkpoint import active_vector, source_hashes, fingerprints_from_sources
from core.optimization.adaptive_policy import ADAPTIVE_CORE
from core.optimization.lns_neighborhood import NEW_CORE

UNIFIED_CORE = {'engine_config.py','engine_model.py','objective_engine.py','engine_checkpoint.py','autoshift_engine.py','schedule_report.py'}

def _manifest(root):
    import json
    return json.loads((Path(root)/'release_manifest.json').read_text(encoding='utf-8'))


def historical_fingerprints(data,prefs,root,version):
    """Reconstruct exact pre-refactor hash from the audited source manifest."""
    if version=='56':
        return fingerprints_from_sources(data,prefs,_manifest(root)['previous_56_sources'])
    if version=='48':
        return fingerprints_from_sources(data,prefs,_manifest(root)['previous_48_sources'])
    if version not in ('472','473','474','475'):
        raise ValueError('Phiên bản checkpoint không hỗ trợ.')
    excluded=(UNIFIED_CORE if version!='475' else set()) | (ADAPTIVE_CORE if version in ('472','473') else set()) | (NEW_CORE if version=='472' else set())
    sources={k:v for k,v in _manifest(root)['legacy_sources'].items()
             if not (k.startswith('core/') and Path(k).name in excluded)}
    return fingerprints_from_sources(data,prefs,sources)


def model_builder_matches(root):
    """Only the exact reviewed release can inherit legacy proofs."""
    try:
        return source_hashes(root)==_manifest(root)['clean_sources']
    except (OSError,ValueError,KeyError,TypeError):
        return False


def migrate_checkpoint(saved,data,prefs,root,hashes):
    if saved is None or saved.base_hash==hashes[0]: return saved,None
    if not model_builder_matches(root): return saved,None
    for version in ('56','48','475','474','473','472'):
        if saved.base_hash==historical_fingerprints(data,prefs,root,version)[0]:
            # Preserve preference hash. Caller still validates assignments and scores.
            return replace(saved,base_hash=hashes[0],proven_prefix=min(saved.proven_prefix,7)),version
    return saved,None


def choose_checkpoint(candidate,old_scores,old_prefix,active,fresh):
    """Pure decision. old_scores must be recalculated from a validated old schedule."""
    new=active_vector(candidate.scores,active)
    old=active_vector(old_scores,active) if old_scores is not None else None
    if old is not None and new>old:
        if not fresh: raise RuntimeError('RESUME không được trả lịch kém checkpoint hợp lệ.')
        return None,'KEPT_OLD',old,new
    if old is not None and new==old and old_prefix>candidate.proven_prefix:
        return replace(candidate,proven_prefix=old_prefix),'SAVED_EQUAL_PROOF',old,new
    return candidate,'SAVED',old,new
