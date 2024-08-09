from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.http import HttpResponseNotAllowed
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Question, Answer
from .forms import QuestionForm, AnswerForm


def index(request):
    page = request.GET.get('page', '1')
    question_list = Question.objects.order_by('-create_date')
    paginator = Paginator(question_list, 10)
    page_obj = paginator.get_page(page)
    context = {'question_list': page_obj}
    return render(request, "board/question_list.html", context)


def detail(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    context = {'question': question}
    return render(request, 'board/question_detail.html', context)


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
def answer_create(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    if request.method == "POST":
        form = AnswerForm(request.POST)
        if form.is_valid():
            answer = form.save(commit=False)
            answer.author = request.user
            answer.create_date = timezone.now()
            answer.question = question
            answer.save()
            return redirect("board:detail", question_id=question.id)
    else:
        form = AnswerForm()
    context = {
        'question' : question,
        'form' : form,
    }
    return render(request, 'board/question_detail.html', context)


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


@login_required(login_url="common:login")
def question_modify(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
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



@login_required(login_url='common:login')
def answer_modify(request, answer_id):
    answer = get_object_or_404(Answer, pk=answer_id)
    if request.user != answer.author:
        messages.error(request, "수정 권한이 없습니다.")
        return redirect('board:detail', question_id=answer.question.id)
    if request.method == "POST":
        form = AnswerForm(request.POST, instance=answer)
        if form.is_valid():
            answer = form.save(commit=False)
            answer.modify_date = timezone.now()
            answer.save()
            return redirect('board:detail', question_id=answer.question.id)
    else:
        form = QuestionForm(instance=answer)
    context = {'form' : form}
    return render(request, 'board/answer_form.html', context)


@login_required(login_url='common:login')
def answer_delete(request, answer_id):
    answer = get_object_or_404(Answer, pk=answer_id)
    if request.user != answer.author:
        messages.error(request, "삭제 권한이 없습니다.")
    else:
        answer.delete()
    return redirect("board:detail", question_id=answer.question.id)


def portfolio(request):
    return render(request, "portfolio.html")
