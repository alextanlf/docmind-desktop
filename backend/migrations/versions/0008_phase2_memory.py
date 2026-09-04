import sqlalchemy as sa
from alembic import op

from app.storage.models import UTCDateTime

revision='0008_phase2_memory'; down_revision='0007_phase2_web_search'; branch_labels=None; depends_on=None
def upgrade():
    op.add_column('sessions', sa.Column('ended_at', UTCDateTime(), nullable=True)); op.add_column('sessions', sa.Column('summary_due_at', UTCDateTime(), nullable=True))
    op.create_table('session_summaries', sa.Column('id',sa.String(36),primary_key=True), sa.Column('session_id',sa.String(36),sa.ForeignKey('sessions.id',ondelete='CASCADE'),unique=True), sa.Column('state',sa.String(16),nullable=False), sa.Column('content',sa.Text), sa.Column('topics_json',sa.Text,server_default='[]'), sa.Column('repository_ids_json',sa.Text,server_default='[]'), sa.Column('error_code',sa.String(128)), sa.Column('retryable',sa.Boolean,server_default=sa.text('0')), sa.Column('created_at',UTCDateTime()), sa.Column('updated_at',UTCDateTime()))
    op.create_table('distillations', sa.Column('id',sa.String(36),primary_key=True), sa.Column('session_id',sa.String(36),sa.ForeignKey('sessions.id',ondelete='SET NULL')), sa.Column('title',sa.String(512),nullable=False), sa.Column('content',sa.Text,nullable=False), sa.Column('key_points_json',sa.Text,server_default='[]'), sa.Column('repository_ids_json',sa.Text,server_default='[]'), sa.Column('state',sa.String(24),nullable=False), sa.Column('target',sa.String(16)), sa.Column('local_path',sa.Text), sa.Column('content_hash',sa.String(128)), sa.Column('remote_document_id',sa.String(255)), sa.Column('remote_url',sa.Text), sa.Column('created_at',UTCDateTime()), sa.Column('updated_at',UTCDateTime()))
    op.create_table('memory_chunks', sa.Column('id',sa.String(36),primary_key=True), sa.Column('summary_id',sa.String(36),sa.ForeignKey('session_summaries.id',ondelete='CASCADE')), sa.Column('distillation_id',sa.String(36),sa.ForeignKey('distillations.id',ondelete='CASCADE')), sa.Column('repository_id',sa.String(36),nullable=False), sa.Column('text',sa.Text,nullable=False), sa.Column('chunk_index',sa.Integer,nullable=False), sa.CheckConstraint('(summary_id IS NOT NULL) != (distillation_id IS NOT NULL)',name='ck_memory_chunks_one_source'))
    op.create_table('memory_vector_cleanups', sa.Column('id',sa.String(36),primary_key=True), sa.Column('collection',sa.String(64),nullable=False), sa.Column('vector_ids_json',sa.Text,nullable=False), sa.Column('created_at',UTCDateTime()))
def downgrade():
    for t in ('memory_vector_cleanups','memory_chunks','distillations','session_summaries'): op.drop_table(t)
    op.drop_column('sessions','summary_due_at'); op.drop_column('sessions','ended_at')
