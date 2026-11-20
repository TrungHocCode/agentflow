"""Authenticated management of the account's own products; one is the default."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import get_current_user_id, get_my_product_repository
from app.modules.competitive_intelligence.models import MyProduct
from app.modules.competitive_intelligence.ports import MyProductRepository

router = APIRouter(prefix="/my-products", tags=["Competitive Intelligence"])


@router.get("", response_model=list[MyProduct])
async def list_my_products(repo: MyProductRepository = Depends(get_my_product_repository),
                           owner: str = Depends(get_current_user_id)) -> list[MyProduct]:
    return await repo.list(owner)


@router.post("", response_model=MyProduct, status_code=201)
async def create_my_product(request: MyProduct, repo: MyProductRepository = Depends(get_my_product_repository),
                            owner: str = Depends(get_current_user_id)) -> MyProduct:
    return await repo.create(owner, request)


@router.patch("/{product_id}", response_model=MyProduct)
async def update_my_product(product_id: UUID, request: MyProduct,
                            repo: MyProductRepository = Depends(get_my_product_repository),
                            owner: str = Depends(get_current_user_id)) -> MyProduct:
    # Full replace of the editable fields; id and is_default in the body are ignored.
    return await repo.update(owner, str(product_id), request)


@router.post("/{product_id}/default", response_model=MyProduct)
async def set_default_my_product(product_id: UUID, repo: MyProductRepository = Depends(get_my_product_repository),
                                 owner: str = Depends(get_current_user_id)) -> MyProduct:
    return await repo.set_default(owner, str(product_id))


@router.delete("/{product_id}", status_code=204)
async def delete_my_product(product_id: UUID, repo: MyProductRepository = Depends(get_my_product_repository),
                            owner: str = Depends(get_current_user_id)) -> Response:
    await repo.delete(owner, str(product_id))
    return Response(status_code=204)
