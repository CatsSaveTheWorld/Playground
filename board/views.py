from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.http import HttpResponseNotAllowed
from .models import Question, Answer
from .forms import QuestionForm, AnswerForm


def index(request):
    question_list = Question.objects.order_by('-create_date')
    context = {'question_list': question_list}
    return render(request, "board/question_list.html", context)


def detail(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    context = {'question': question}
    return render(request, 'board/question_detail.html', context)


def question_create(request):
    if request.method == "POST":
        form = QuestionForm(request.POST)
        if form.is_valid():
            question = form.save(commit=False)      # 현재 QuestionForm에는 subject, content 속성만 정의되어 있고 create_date에 값이 없음. commit=True 시 오류 발생
            question.create_date = timezone.now()
            question.save()
            return redirect("board:index")
    else:
        form = QuestionForm()
    context = {"form" : form}
    return render(request, 'board/question_form.html', context)


def answer_create(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    if request.method == "POST":
        form = AnswerForm(request.POST)
        if form.is_valid():
            answer = form.save(commit=False)
            answer.create_date = timezone.now()
            answer.question = question
            answer.save()
            return redirect("board:detail", question_id=question.id)
    else:
        return HttpResponseNotAllowed("Only POST is possible.")
    context = {
        'question' : question,
        'form' : form,
    }
    return render(request, 'board/question_detail.html', context)


def portfolio(request):
    return render(request, "portfolio.html")
