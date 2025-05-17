{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "CASE",
      "operator": "CASE",
      "type": "CHAR",
      "operands": [
        {
          "kind": "AND",
          "operator": "AND",
          "type": "BOOLEAN",
          "operands": [
            {
              "kind": "GREATER_THAN_OR_EQUAL",
              "operator": ">=",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 14,
                  "name": "$14",
                  "type": "FLOAT"
                },
                {
                  "kind": "LITERAL",
                  "value": 3.5,
                  "type": "DECIMAL",
                  "nullable": false,
                  "precision": 2
                }
              ]
            },
            {
              "kind": "LESS_THAN_OR_EQUAL",
              "operator": "<=",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 14,
                  "name": "$14",
                  "type": "FLOAT"
                },
                {
                  "kind": "LITERAL",
                  "value": 5.5,
                  "type": "DECIMAL",
                  "nullable": false,
                  "precision": 2
                }
              ]
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
              "value": "normal",
              "type": "CHAR",
              "nullable": false,
              "precision": 6
            }
          ]
        },
        {
          "kind": "LITERAL",
          "value": "abnormal",
          "type": "CHAR",
          "nullable": false,
          "precision": 8
        }
      ]
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "EQUALS",
        "operator": "=",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "OTHER_FUNCTION",
            "operator": "STRFTIME",
            "type": "VARCHAR",
            "operands": [
              {
                "kind": "LITERAL",
                "value": "%Y",
                "type": "CHAR",
                "nullable": false,
                "precision": 2
              },
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "VARCHAR",
                "operands": [
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "TIMESTAMP",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 2,
                        "name": "$2",
                        "type": "DATE"
                      }
                    ]
                  }
                ]
              }
            ]
          },
          {
            "kind": "LITERAL",
            "value": "1982",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
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
                "kind": "INPUT_REF",
                "index": 7,
                "name": "$7",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Patient",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "Laboratory",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}