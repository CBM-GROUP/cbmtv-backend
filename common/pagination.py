from rest_framework.pagination import PageNumberPagination

class StandardPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    # Without a ceiling, `?page_size=1000000` makes any list endpoint serialize
    # the whole table in one request. Clients that need everything should walk
    # the `next` links instead of asking for one enormous page.
    max_page_size = 100
    
