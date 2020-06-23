"""move groups accessrights to users_to_group

Revision ID: 72f8be1c4838
Revises: bb305829cf83
Create Date: 2020-06-02 13:01:35.073902+00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '72f8be1c4838'
down_revision = 'bb305829cf83'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('groups', 'access_rights')
    op.add_column('user_to_groups', sa.Column('access_rights', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text('\'{"read": true, "write": false, "delete": false}\'::jsonb'), nullable=False))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('user_to_groups', 'access_rights')
    op.add_column('groups', sa.Column('access_rights', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), autoincrement=False, nullable=False))
    # ### end Alembic commands ###
