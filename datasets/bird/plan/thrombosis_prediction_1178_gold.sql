{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "CASE",
      "operator": "CASE",
      "type": "CHAR",
      "operands": [
        {
          "kind": "LESS_THAN",
          "operator": "<",
          "type": "BOOLEAN",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 12,
              "name": "$12",
              "type": "INTEGER"
            },
            {
              "kind": "LITERAL",
              "value": 250,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        },
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "CHAR",
          "operands": [
            {
              "kind": "LITERAL",
              "value": "Normal",
              "type": "CHAR",
              "nullable": false,
              "precision": 6
            }
          ]
        },
        {
          "kind": "LITERAL",
          "value": "Abnormal",
          "type": "CHAR",
          "nullable": false,
          "precision": 8
        }
      ]
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 0,
                "name": "$0",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 2927464,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 1,
                "name": "$1",
                "type": "DATE"
              },
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "DATE",
                "operands": [
                  {
                    "kind": "LITERAL",
                    "value": "1995-09-04",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "Laboratory",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}