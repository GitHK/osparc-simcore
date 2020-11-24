"""change file_size to big integer

Revision ID: c8a7073deebb
Revises: 64e91497d257
Create Date: 2020-11-04 13:56:31.354965+00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8a7073deebb'
down_revision = '64e91497d257'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column("file_meta_data", "file_size", type_=sa.BigInteger, existing_type=sa.Integer)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column("file_meta_data", "file_size", type_=sa.Integer, existing_type=sa.BigInteger)
    # ### end Alembic commands ###