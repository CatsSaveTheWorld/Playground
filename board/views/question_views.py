from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from ..forms import QuestionForm


@login_required(login_url="common:login")
def question_create(request):
    if request.method == "POST":
        form = QuestionForm(request.POST)
        if form.is_valid():
            question = form.save(commit=False)      # 현재 QuestionForm에는 subject, content 속성만 정의되어 있고 create_date에 값이 없음. commit=True 시 오류 발생
            question.author = request.user
            question.create_date = timezone.now()
            question.save()
            return redirect("board:index")
    else:
        form = QuestionForm()
    context = {"form" : form}
    return render(request, 'board/question_form.html', context)


@login_required(login_url="common:login")
def question_modify(request, question_id):
    question = get_object_or_404(request, question_id)
    if request.user != question.author:
        messages.error(request, "수정 권한이 없습니다.")
        return redirect("board:detail", question_id=question.id)
    if request.method == "POST":
        form = QuestionForm(request.POST, instance=question)
        if form.is_valid():
            question = form.save(commit=False)
            question.modify_date = timezone.now()
            question.save()
            return redirect("board:detail", question_id=question.id)
    else:
        form = QuestionForm(instance=question)
    context = {'form' : form}
    return render(request, 'board/question_form.html', context)


@login_required(login_url='common:login')
def question_delete(request, question_id):
    question = get_object_or_404(request, pk=question_id)
    if request.user != question.quthor:
        messages.error(request, "삭제 권한이 없습니다.")
        return redirect('board:detail', question_id=question.id)
    question.delete()
    return redirect('board:index')











