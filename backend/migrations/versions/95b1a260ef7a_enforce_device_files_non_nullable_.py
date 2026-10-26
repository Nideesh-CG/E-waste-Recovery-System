"""enforce_device_files_non_nullable_constraints

Revision ID: 95b1a260ef7a
Revises: 41179279b201
Create Date: 2026-10-14 22:21:01.111072

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '95b1a260ef7a'
down_revision = '41179279b201'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('device_files', schema=None) as batch_op:
        batch_op.alter_column('device_id',
               existing_type=sa.VARCHAR(length=64),
               nullable=False)
        batch_op.alter_column('content_type',
               existing_type=sa.VARCHAR(length=128),
               nullable=False)
        batch_op.alter_column('file_size',
               existing_type=sa.INTEGER(),
               nullable=False)
        batch_op.alter_column('is_private',
               existing_type=sa.BOOLEAN(),
               nullable=False)


def downgrade():
    with op.batch_alter_table('device_files', schema=None) as batch_op:
        batch_op.alter_column('is_private',
               existing_type=sa.BOOLEAN(),
               nullable=True)
        batch_op.alter_column('file_size',
               existing_type=sa.INTEGER(),
               nullable=True)
        batch_op.alter_column('content_type',
               existing_type=sa.VARCHAR(length=128),
               nullable=True)
        batch_op.alter_column('device_id',
               existing_type=sa.VARCHAR(length=64),
               nullable=True)
