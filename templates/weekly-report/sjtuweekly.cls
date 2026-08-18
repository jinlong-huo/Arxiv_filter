\NeedsTeXFormat{LaTeX2e}[1999/12/01]
\ProvidesClass{sjtuweekly}[2026/06/01 v2.0 SJTU Weekly Report]

% ======= 基类 =======
\PassOptionsToPackage{AutoFakeBold}{xeCJK}
\LoadClass[a4paper, UTF8, oneside, zihao=-4]{ctexart}

% ======= 页面边距 =======
\RequirePackage[top=2.5cm, bottom=2.5cm, left=2.5cm, right=2.5cm,
  headheight=2.0cm, footskip=1.2cm, heightrounded]{geometry}

% ======= 宏包 =======
\RequirePackage{amsmath, amsfonts, amssymb}
\RequirePackage{graphicx}
\graphicspath{{figures/}{figure/}{pictures/}{pic/}{pics/}{image/}{images/}}
\RequirePackage[svgnames]{xcolor}
\RequirePackage{float}
\RequirePackage{booktabs}
\RequirePackage{enumitem}
\RequirePackage{makecell}
\RequirePackage{array}
\RequirePackage{setspace}

% ======= 页眉页脚 =======
\RequirePackage{fancyhdr}
\RequirePackage{lastpage}

\pagestyle{fancy}
\fancyhf{}

\lhead{\zihao{-5}\songti \enspace 工作周报}
\rhead{\zihao{-5}\songti\thepage}
\renewcommand{\headrulewidth}{0.4pt}

% ======= 信息变量 =======
\newcommand{\member}[1]{\def\@member{#1}}
\newcommand{\@member}{}
\newcommand{\weeknum}[1]{\def\@weeknum{#1}}
\newcommand{\@weeknum}{}
\newcommand{\reportdate}[1]{\def\@reportdate{#1}}
\newcommand{\@reportdate}{\today}

% 校徽
% \newcommand{\sjtuLogo}{sjtubannerblue.png}
% \newcommand{\setLogo}[1]{\renewcommand{\sjtuLogo}{#1}}

% 不显示章节编号（但加入目录）
\AtBeginDocument{\setcounter{secnumdepth}{0}}

% 目录名
\renewcommand{\contentsname}{目\quad 录}

% ======= 周报封面（第一页） =======
\newcommand{\makeweeklytitle}{%
  \thispagestyle{empty}
  % logo 左上角（贴顶）
  \ifdefined\sjtuLogo
    \vspace*{-1.2cm}
    \noindent\includegraphics[height=2cm]{\sjtuLogo}
    \vspace{2.0cm}
  \fi
  % 标题
  \begin{center}
    {\zihao{2}\songti\bfseries 工作汇报}
  \end{center}
  \vspace{0.8cm}
  % 信息（分行）
  \begin{center}
    {\zihao{4}\songti
      \begin{tabular}{r@{\quad}l}
        成员 & \@member \\[0.35cm]
        % 周次 & 第\@weeknum 周 \\[0.35cm]
        日期 & \@reportdate \\
      \end{tabular}
    }
  \end{center}
  \vspace{0.6cm}
  % 简易目录（可点击跳转）
  {\zihao{-4}\songti\tableofcontents}
  \newpage
  \pagestyle{fancy}
}

% ======= 参考文献 =======
\RequirePackage[sort&compress]{natbib}
\newcommand{\bibfile}[1]{\def\@bibfile{#1}}
\newcommand{\@bibfile}{bib/database}
\newcommand{\printbib}{%
  \clearpage
  \phantomsection
  \addcontentsline{toc}{section}{参考文献}
  \renewcommand{\refname}{参考文献}
  \bibliographystyle{unsrt}
  \bibliography{\@bibfile}
}

% ======= 代码展示 =======
\RequirePackage{listings}
\lstset{
  breaklines=true,
  columns=fixed,
  numbers=left,
  numberstyle=\tiny\color{gray},
  frame=none,
  backgroundcolor=\color[RGB]{245,245,244},
  keywordstyle=\color[RGB]{40,40,255},
  commentstyle=\it\color[RGB]{0,96,96},
  stringstyle=\rmfamily\slshape\color[RGB]{128,0,0},
  showstringspaces=false,
  language=python,
  alsolanguage=[ANSI]C,
  alsolanguage=java,
  alsolanguage=XML,
  alsolanguage=c++,
  alsolanguage=matlab,
}

% ======= 超链接 =======
\RequirePackage[unicode, psdextra]{hyperref}
\hypersetup{hidelinks, breaklinks=true, bookmarksopen=false}

% ======= 图表编号 =======
\numberwithin{figure}{section}
\numberwithin{table}{section}
\numberwithin{equation}{section}

% ======= 引用格式 =======
\RequirePackage[nameinlink]{cleveref}
\crefformat{equation}{#2式(#1)#3}
\crefformat{figure}{#2图#1#3}
\crefformat{table}{#2表#1#3}

% ======= 列表样式 =======
\setenumerate[1]{itemsep=0pt, partopsep=0pt, parsep=\parskip, topsep=5pt}
\setitemize[1]{itemsep=0pt, partopsep=0pt, parsep=\parskip, topsep=0pt}

% ======= 图表标题 =======
\RequirePackage[font=small, labelfont=bf]{caption}

% ======= 正文行距 =======
\linespread{1.35}\selectfont

\endinput
