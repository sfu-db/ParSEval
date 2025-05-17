{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "CASE",
      "operator": "CASE",
      "type": "BOOLEAN",
      "operands": [
        {
          "kind": "OR",
          "operator": "OR",
          "type": "BOOLEAN",
          "operands": [
            {
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
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "F",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "GREATER_THAN",
                  "operator": ">",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 15,
                      "name": "$15",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "LITERAL",
                      "value": 6.5,
                      "type": "DECIMAL",
                      "nullable": false,
                      "precision": 2
                    }
                  ]
                }
              ]
            },
            {
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
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "M",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "LESS_THAN",
                  "operator": "<",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 15,
                      "name": "$15",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "LITERAL",
                      "value": 8.0,
                      "type": "DECIMAL",
                      "nullable": false,
                      "precision": 2
                    }
                  ]
                }
              ]
            }
          ]
        },
        {
          "kind": "LITERAL",
          "value": true,
          "type": "BOOLEAN",
          "nullable": false,
          "precision": 1
        },
        {
          "kind": "LITERAL",
          "value": false,
          "type": "BOOLEAN",
          "nullable": false,
          "precision": 1
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
            "kind": "INPUT_REF",
            "index": 0,
            "name": "$0",
            "type": "INTEGER"
          },
          {
            "kind": "LITERAL",
            "value": 57266,
            "type": "INTEGER",
            "nullable": false,
            "precision": 10
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