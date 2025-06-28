from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404
from ..models import Question


def index(request):
    page = request.GET.get('page', '1')
    question_list = Question.objects.order_by('-create_date')
    paginator = Paginator(question_list, 10)
    page_obj = paginator.get_page(page)

    # 👇 여기에 권한 검사 추가
    has_iot_permission = request.user.has_perm('smartcore.can_control_iot_devices')

    context = {
        'question_list': page_obj,
        'has_iot_permission': has_iot_permission,
    }
    return render(request, "board/question_list.html", context)


def detail(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    context = {'question': question}
    return render(request, 'board/question_detail.html', context)










